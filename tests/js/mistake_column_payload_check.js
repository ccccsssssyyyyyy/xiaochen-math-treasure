/**
 * 错题工作台「分栏 / 人工栏目」端到端校验 —— 真实执行 mistake.js，不靠正则猜源码。
 *
 * 为什么要有这个脚本：
 *   分栏这一层的错法都很安静：栏目改完没标记待重切、双栏页的题块仍按整页宽渲染、
 *   拖完分栏线顺手把这个 click 当成「选中题块」—— 这些都不会报错，只会让切出来的
 *   题块位置不对，而且要到 PDF 出来了才发现。所以这里用假 DOM 把 mistake.js
 *   整个加载进 vm 沙箱，真实跑「渲染 → 拖动分栏线 → 按新栏目重切」三条链路，
 *   直接断言 DOM 输出与 fetch 请求体。
 *
 * 用法: node tests/js/mistake_column_payload_check.js [mistake.js 路径]
 * 退出码: 0 全通过 / 1 有失败
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const mistakePath = process.argv[2]
  || path.join(__dirname, '..', '..', 'static', 'js', 'mistake.js');
const src = fs.readFileSync(mistakePath, 'utf8');

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}

// ---------------------------------------------------------------- 假 DOM

function makeElement(id) {
  const classes = new Set();
  return {
    id: id,
    innerHTML: '',
    textContent: '',
    value: '',
    style: {},
    dataset: {},
    handlers: {},
    classList: {
      add: function () { for (const c of arguments) classes.add(c); },
      remove: function () { for (const c of arguments) classes.delete(c); },
      toggle: function (c, on) { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
      contains: function (c) { return classes.has(c); }
    },
    setAttribute: function (name, value) { this.dataset['attr_' + name] = value; },
    getAttribute: function (name) { return this.dataset['attr_' + name]; },
    removeAttribute: function (name) { delete this.dataset['attr_' + name]; },
    addEventListener: function (type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); },
    querySelector: function () { return null; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: 1000, height: 2000, right: 1000, bottom: 2000 }; }
  };
}

const elements = {};
function elementFor(id) {
  if (!elements[id]) elements[id] = makeElement(id);
  return elements[id];
}

const requests = [];
const documentHandlers = {};

const sandbox = {
  console: console,
  setTimeout: function () { return 0; },
  clearTimeout: function () {},
  setInterval: function () { return 0; },
  clearInterval: function () {},
  fetch: function (url, options) {
    requests.push({ url: url, options: options || {} });
    return Promise.resolve({
      ok: true,
      status: 200,
      json: function () { return Promise.resolve({ status: 'success', task_id: 'task-1' }); }
    });
  },
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  document: {
    getElementById: elementFor,
    addEventListener: function (type, fn) { (documentHandlers[type] = documentHandlers[type] || []).push(fn); },
    removeEventListener: function (type, fn) {
      const list = documentHandlers[type] || [];
      const index = list.indexOf(fn);
      if (index >= 0) list.splice(index, 1);
    },
    querySelector: function () { return null; },
    documentElement: { classList: { add: function () {}, remove: function () {} } }
  }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
// 沙箱也要有页面里先加载的基础模块，否则 window.MathRender 是 undefined
require('./sandbox_base').loadBaseModules(sandbox);
vm.runInContext(src, sandbox, { filename: 'mistake.js' });

const store = sandbox.MistakeStore;
const overlay = elementFor('mistakePageOverlay');

function resetRequests() { requests.length = 0; }

/** 造一页 + 该页的题块，双栏页里左右栏各一块。 */
function loadPage(pageLayout, options) {
  const opts = options || {};
  store.batch = { id: 7, subject: 'physics', title: '测试卷' };
  store.pageIndex = 0;
  store.pages = [{
    page_no: 1,
    url: '/page_1.png',
    layout: pageLayout,
    column_boundary: pageLayout.mode === 'double' ? pageLayout.boundary : 1.0
  }];
  store.records = opts.records || [
    { id: 11, page_no: 1, block_index: 0, block_x_start: 0.0, block_x_end: 0.492, block_y_start: 0.05, block_y_end: 0.30, column_index: 1, grad_status: 'unknown' },
    { id: 12, page_no: 1, block_index: 1, block_x_start: 0.492, block_x_end: 1.0, block_y_start: 0.05, block_y_end: 0.30, column_index: 2, grad_status: 'unknown' }
  ];
  store.pageLayouts = {};
  store.layoutDirty = {};
}

/** 点在某个题块上的 click：用来验证拖拽之后紧跟的 click 被吞掉。 */
function clickOnRecord(x, y, recordId) {
  overlay.handlers.click.forEach(function (fn) {
    fn({
      target: {
        closest: function (sel) {
          return sel === '[data-record]' ? { dataset: { record: String(recordId) } } : null;
        }
      },
      clientX: x, clientY: y
    });
  });
}

function dragTo(x) {
  overlay.handlers.pointerdown.forEach(function (fn) {
    fn({
      target: { closest: function (sel) { return sel === '[data-layout-handle]' ? {} : null; } },
      clientX: 492, clientY: 0, preventDefault: function () {}
    });
  });
  (documentHandlers.pointermove || []).slice().forEach(function (fn) { fn({ clientX: x }); });
  (documentHandlers.pointerup || []).slice().forEach(function (fn) { fn({}); });
}

function lastBody() {
  if (!requests.length) return null;
  return JSON.parse(requests[requests.length - 1].options.body || '{}');
}

// 让 fetch 的 .then 跑完：切线/栏目的清账发生在响应回调里，不同步
const tick = () => new Promise(function (resolve) { setImmediate(resolve); });

(async function main() {

// ---------------------------------------------------------------- 1. 渲染口径

console.log('\n[1] 双栏页的渲染口径');
loadPage({ mode: 'double', boundary: 0.492, source: 'visual', confidence: 0.9 });
sandbox.setMistakeLayout('auto');   // 触发一次 renderPageView，走自动判栏分支
let html = overlay.innerHTML;
check('自动双栏：画了分栏线', html.indexOf('data-layout-handle="1"') >= 0);
check('自动双栏：分栏线落点 = 判定值 0.492', html.indexOf('calc(49.2% - 7px)') >= 0, html.slice(0, 200));
check('自动双栏：提示显示判定来源与置信度',
  elementFor('mistakeLayoutHint').textContent.indexOf('自动判定：双栏（图像 90%）') === 0,
  elementFor('mistakeLayoutHint').textContent);
check('题块按栏定位（左栏 0%–49.2%）', html.indexOf('left:0%;width:49.2%') >= 0);
check('题块按栏定位（右栏 49.2%–100%）', html.indexOf('left:49.2%;width:50.8%') >= 0);
check('题块不再整页宽涂色', html.indexOf('class="absolute left-0 right-0 cursor-pointer"') < 0);
check('题块 title 带栏号', html.indexOf('· 左栏') >= 0 && html.indexOf('· 右栏') >= 0);

console.log('\n[2] 单栏页的渲染口径');
loadPage({ mode: 'single', boundary: 1.0, source: 'text_layer', confidence: 1.0 },
  { records: [{ id: 11, page_no: 1, block_index: 0, block_x_start: 0, block_x_end: 1, block_y_start: 0.05, block_y_end: 0.30, column_index: 0, grad_status: 'unknown' }] });
sandbox.setMistakeLayout('auto');
html = overlay.innerHTML;
check('单栏页不画分栏线', html.indexOf('data-layout-handle') < 0);
check('单栏页题块仍是整页宽', html.indexOf('left:0%;width:100%') >= 0);
check('提示显示文本层判定', elementFor('mistakeLayoutHint').textContent.indexOf('自动判定：单栏（文本层 100%）') === 0,
  elementFor('mistakeLayoutHint').textContent);

// ---------------------------------------------------------------- 3. 请求体口径

console.log('\n[3] 请求体：page_layouts 口径');
loadPage({ mode: 'double', boundary: 0.492, source: 'visual', confidence: 0.9 });
sandbox.setMistakeLayout('double');                    // 人工指定双栏（未拖线 → 只表态）
resetRequests();
sandbox.startMistakeCut();
let body = lastBody();
check('人工栏目一起带上（只表态时发字符串）', body.page_layouts && body.page_layouts['1'] === 'double', JSON.stringify(body.page_layouts));
check('切题请求不再夹带切线字段',
  body.page_cuts === undefined && body.column_cuts === undefined, JSON.stringify(body));
check('请求打到切题接口', requests[requests.length - 1].url === '/api/mistakes/batches/7/cut');

// ---------------------------------------------------------------- 4. 拖动分栏线

console.log('\n[4] 拖动分栏线');
loadPage({ mode: 'double', boundary: 0.492, source: 'visual', confidence: 0.9 });
dragTo(600);                                           // 拖到 60%
check('拖动改的是人工分栏线', JSON.stringify(store.pageLayouts[1]) === JSON.stringify({ mode: 'double', boundary: 0.6 }),
  JSON.stringify(store.pageLayouts));
check('拖动后标记待重切', store.layoutDirty[1] === true);
html = overlay.innerHTML;
check('分栏线跟着走', html.indexOf('calc(60% - 7px)') >= 0);
check('拖动后提示改为「人工指定 · 待重切」',
  elementFor('mistakeLayoutHint').innerHTML.indexOf('人工指定：双栏 · 待重切') >= 0,
  elementFor('mistakeLayoutHint').innerHTML);
clickOnRecord(600, 500, 11);                           // 紧接着这个 click 必须被吞掉
check('拖完紧跟的 click 不选中题块', store.selectedRecordId === null, String(store.selectedRecordId));
resetRequests();
sandbox.startMistakeCut();                             // 「按新栏目重切」
body = lastBody();
check('人工分栏线随重切提交', JSON.stringify((body.page_layouts || {})['1']) === JSON.stringify({ mode: 'double', boundary: 0.6 }),
  JSON.stringify(body.page_layouts));
await tick();

console.log('\n[5] 切完清状态：人工栏目留着');
check('人工栏目保留', JSON.stringify(store.pageLayouts[1]) === JSON.stringify({ mode: 'double', boundary: 0.6 }));
check('待重切标记已清', store.layoutDirty[1] === undefined);

console.log('\n[6] 改回自动：仍要重切，且不再提交栏目');
sandbox.setMistakeLayout('auto');
check('自动态不提交 page_layouts', JSON.stringify(store.pageLayouts) === '{}', JSON.stringify(store.pageLayouts));
check('自动态依旧标记待重切', store.layoutDirty[1] === true);
check('提示按钮为「按新栏目重切」', elementFor('mistakeLayoutHint').innerHTML.indexOf('按新栏目重切') >= 0);
resetRequests();
sandbox.startMistakeCut();
body = lastBody();
check('整批重切不再夹带 page_layouts', body.page_layouts === undefined, JSON.stringify(body));

console.log('\n' + '='.repeat(60));
if (fail === 0) {
  console.log('全部通过：' + pass + ' 项');
  process.exit(0);
}
console.log('失败 ' + fail + ' 项 / 共 ' + (pass + fail) + ' 项');
process.exit(1);

})();
