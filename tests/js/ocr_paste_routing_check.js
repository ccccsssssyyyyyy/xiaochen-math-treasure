// ocr.js 的全局粘贴路由：图省事的做法是断言源码里有那几行字符串，但字符串在、
// 分支却走不到（顺序错了、条件写反了）是这类改动的典型翻车方式。所以这里把 ocr.js
// 真加载进 vm、真调 setupUploadHandlers()、真丢一个 paste 事件进去，看它把图交给了谁。
//
// 关注的就一件事：**错题审校页打开时，粘贴的截图必须走「解析截图识别」**，
// 而不是掉进下面的兜底分支被当成题库插图收走（那会让用户回到录入页时莫名多一张插图）。
//
// 用法：node tests/js/ocr_paste_routing_check.js [ocr.js 路径]
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ocrPath = process.argv[2] || path.join(__dirname, '..', '..', 'static', 'js', 'ocr.js');
const src = fs.readFileSync(ocrPath, 'utf8');

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) {
    pass += 1;
    console.log('  PASS  ' + name);
  } else {
    fail += 1;
    console.log('  FAIL  ' + name + (extra === undefined ? '' : '\n        -> ' + extra));
  }
}

const noop = function () {};

// 每个 id 一个稳定替身。offsetParent 用真值表示"在屏幕上看得见"，
// ocr.js 判断可见性全靠它（null = 隐藏）。
function makeEl(id) {
  const classes = new Set();
  return {
    id: id,
    value: '', checked: false, className: '', innerHTML: '', textContent: '',
    style: {}, dataset: {},
    offsetParent: null,
    handlers: {},
    classList: {
      add: function () { for (const c of arguments) classes.add(c); },
      remove: function () { for (const c of arguments) classes.delete(c); },
      contains: function (c) { return classes.has(c); },
      toggle: noop
    },
    addEventListener: function (type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); },
    removeEventListener: noop,
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    setAttribute: noop, removeAttribute: noop,
    appendChild: noop, dispatchEvent: noop,
    getBoundingClientRect: function () {
      return { left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100 };
    }
  };
}

const elements = {};
function elementFor(id) {
  if (!elements[id]) elements[id] = makeEl(id);
  return elements[id];
}

const windowPaste = [];
let ocrCalls = [];
let toasts = [];
let fallbackCalls = [];
let preventDefaulted = false;

const sandbox = {
  console: console,
  document: {
    getElementById: elementFor,
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    createElement: function () { return makeEl(''); },
    addEventListener: noop,
    removeEventListener: noop,
    documentElement: { classList: { add: noop, remove: noop } },
    body: makeEl('body'),
    activeElement: null
  },
  localStorage: { getItem: function () { return null; }, setItem: noop, removeItem: noop },
  fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({}); } }); },
  setTimeout: setTimeout, clearTimeout: clearTimeout,
  setInterval: setInterval, clearInterval: clearInterval,
  setImmediate: setImmediate,
  FormData: function () { this.append = noop; this.get = function () { return null; }; },
  AbortController: function () { this.signal = { aborted: false }; this.abort = noop; },
  FileReader: function () { this.readAsDataURL = noop; },
  alert: noop, confirm: function () { return true; }, prompt: function () { return null; },
  Image: function () { this.addEventListener = noop; },
  Event: function () {}, CustomEvent: function () {},
  addEventListener: function (type, fn) { if (type === 'paste') windowPaste.push(fn); },
  removeEventListener: noop
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;

vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: 'ocr.js' });

// 兜底分支会走到 uploadIllustration：桩掉，用它的调用作为"图被收进题库录入区"的证据。
sandbox.uploadIllustration = function (file) { fallbackCalls.push(file); };
// 审校页侧的处理函数：由 mistake.js 挂上，这里换成探针。
sandbox.runReviewAnswerOcr = function (file) { ocrCalls.push(file); };
sandbox.showToast = function (message, type) { toasts.push([message, type]); };

console.log('\n[A] 装配 ocr.js 的上传/粘贴处理器');
let booted = true;
try {
  sandbox.setupUploadHandlers();
} catch (e) {
  booted = false;
  console.log('  setupUploadHandlers() 抛错: ' + (e && e.message));
}
check('setupUploadHandlers() 能跑通（import.js 就是靠它装配的）', booted);
check('全局 paste 路由已注册', windowPaste.length === 1, '注册了 ' + windowPaste.length + ' 个');

const imageFile = { type: 'image/png', name: 'clip.png' };
function pasteEvent(item) {
  preventDefaulted = false;
  return {
    clipboardData: { items: [item || { kind: 'file', type: 'image/png', getAsFile: function () { return imageFile; } }] },
    preventDefault: function () { preventDefaulted = true; }
  };
}
function firePaste(item) {
  ocrCalls = [];
  toasts = [];
  fallbackCalls = [];
  const ev = pasteEvent(item);
  windowPaste.forEach(function (fn) { fn(ev); });
  return ev;
}

console.log('\n[B] 审校页打开（mistakeReviewView 可见）');
elementFor('mistakeReviewView').offsetParent = {};   // 可见
elementFor('mrContent').offsetParent = {};           // 题面框也在屏幕上
sandbox.document.activeElement = null;               // 焦点不在任何输入框

firePaste();
check('粘贴的截图交给了解析截图识别', ocrCalls.length === 1 && ocrCalls[0] === imageFile,
  '收到 ' + ocrCalls.length + ' 次，文件=' + JSON.stringify(ocrCalls[0]));
check('撕掉默认行为（否则粘贴内容会同时落进别处）', preventDefaulted);
check('没有掉进库图兜底分支', fallbackCalls.length === 0, String(fallbackCalls.length));

console.log('\n[C] 焦点在题面框：不抢，只提示（不能把题面截图填进解析）');
sandbox.document.activeElement = elementFor('mrContent');
firePaste();
check('焦点在题面框时不写解析', ocrCalls.length === 0, String(ocrCalls.length));
check('提示走「补图形 / 重新识别这道」', toasts.length === 1 && /补图形/.test(toasts[0][0]),
  JSON.stringify(toasts));
check('也不掉进插图兜底', fallbackCalls.length === 0, String(fallbackCalls.length));
check('同样撕掉默认行为', preventDefaulted);

console.log('\n[D] 审校页关着：路由不该管它（走原有兜底，属 ocr.js 既有行为）');
elementFor('mistakeReviewView').offsetParent = null;   // 隐藏
sandbox.document.activeElement = null;
firePaste();
check('审校页隐藏时不碰解析识别', ocrCalls.length === 0, String(ocrCalls.length));

console.log('\n[E] 非图片粘贴：谁都不该被叫起来');
elementFor('mistakeReviewView').offsetParent = {};
firePaste({ kind: 'string', type: 'text/plain', getAsFile: function () { return null; } });
check('纯文本粘贴不触发解析识别', ocrCalls.length === 0, String(ocrCalls.length));

console.log('\n结果: ' + pass + ' passed, ' + fail + ' failed');
process.exit(fail === 0 ? 0 : 1);
