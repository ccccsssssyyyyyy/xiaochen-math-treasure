/**
 * 错题工作台「人工框选」端到端校验 —— 真实执行 mistake.js，不靠正则猜源码。
 *
 * 为什么要有这个脚本：
 *   框选这一层的错法同样很安静 —— 框拖到页外算出了负坐标（后端整个丢掉，用户只看到
 *   「框没了」）、自动块被框压住半截后凭空多出一个碎片卡片、空框列表被当成「没改」
 *   而没有发出去（于是清空按钮看起来没反应）。这些都不报错，只会让题块位置错掉。
 *   所以这里用假 DOM 把 mistake.js 整个加载进 vm 沙箱，真实跑「画框 → 移动 → 改大小
 *   → 删除 → 应用」全链路，直接断言 store 状态、overlay 的 HTML 与 fetch 请求体。
 *
 * 用法: node tests/js/mistake_box_payload_check.js [mistake.js 路径]
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
    className: '',
    disabled: false,
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
    // 1000×2000 的页面：所有归一化坐标断言都按这个尺寸折算
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

// 自动保存是防抖的（停手 1 秒才发），所以必须有能手动推进的定时器 —— 直接用
// 空实现的 setTimeout 会让「等 1 秒」这件事永远不发生，测试就退化成了「什么都不发」。
const timers = [];
let timerSeq = 1;

/** 把当前排队的定时器全部跑掉（模拟「用户停手 1 秒」）。 */
function runTimers() {
  const queued = timers.splice(0, timers.length);
  queued.forEach(function (timer) { timer.fn(); });
}

function pendingTimerCount() { return timers.length; }

/** 置为 true 时下一次 fetch 返回失败，用来测错误分支。 */
let failNext = false;
const failStatus = { code: 400, message: '框选接口拒绝了这次请求。' };

/** 设上之后，GET /api/mistakes/batches/<id> 返回这份批次详情（回显测试用）。 */
let detailPayload = null;

function jsonResponse(ok, status, data) {
  return Promise.resolve({ ok: ok, status: status, json: function () { return Promise.resolve(data); } });
}

/**
 * 真后端在 GET 批次详情时会回显该页的 manual_boxes（记进 analysis.json 那份）。
 * syncBoxState 正是靠它把本地框对齐到服务端 —— 假后端不回显的话，本地框会被一份
 * 空的 pages 抹掉，测出来的时序和线上不是一回事。
 */
function echoDetailPayload() {
  const posts = blockPosts();
  const boxes = posts.length ? (JSON.parse(posts[posts.length - 1].options.body).boxes || []) : [];
  return {
    batch: { id: 7, subject: 'physics', title: '测试卷', page_count: 1, total: 1 },
    records: [],
    pages: [{
      page_no: 1, url: '/page_1.png', width: 1000, height: 2000,
      layout: { mode: 'single', boundary: 1.0, source: 'text_layer', confidence: 1.0 },
      column_boundary: 1.0, snap_points: [], gap_candidates: [],
      column_snap_points: {}, column_gap_candidates: {}, manual_boxes: boxes
    }],
    question_types: [], mistake_reasons: []
  };
}

const sandbox = {
  console: console,
  setTimeout: function (fn, delay) {
    const id = timerSeq++;
    timers.push({ id: id, fn: fn, delay: delay || 0 });
    return id;
  },
  clearTimeout: function (id) {
    const index = timers.findIndex(function (timer) { return timer.id === id; });
    if (index >= 0) timers.splice(index, 1);
  },
  setInterval: function () { return 0; },
  clearInterval: function () {},
  fetch: function (url, options) {
    requests.push({ url: url, options: options || {} });
    if (failNext) {
      failNext = false;
      return jsonResponse(false, failStatus.code, { status: 'error', message: failStatus.message });
    }
    // 批次详情：只有「取详情」这一个入口给它，POST 到 /cut 或 /blocks 不算
    if (/^\/api\/mistakes\/batches\/\d+$/.test(url)) {
      return jsonResponse(true, 200, detailPayload || echoDetailPayload());
    }
    // 任务轮询：立刻回报完成，让 onDone 里的 refreshMistakeDetail 真的跑到
    if (/^\/api\/tasks\//.test(url)) {
      return jsonResponse(true, 200, { status: 'completed', block_count: 4, steps: [] });
    }
    return jsonResponse(true, 200, { status: 'success', task_id: 'task-1', box_count: 1, block_count: 4, removed: 3 });
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
const math = sandbox.MistakeBoxMath;
const overlay = elementFor('mistakePageOverlay');
const cardList = elementFor('mistakeCardList');

function resetRequests() { requests.length = 0; }

/** 打到「本页框选」接口的请求（排除取详情、任务轮询那些）。 */
function blockPosts() {
  return requests.filter(function (r) { return /\/pages\/\d+\/blocks$/.test(r.url); });
}

/** 造一页 + 该页的题块；页尺寸固定 1000×2000，方便把比例折算成像素。 */
function loadPage(options) {
  const opts = options || {};
  store.batch = { id: 7, subject: 'physics', title: '测试卷' };
  store.pageIndex = 0;
  const layout = opts.layout || { mode: 'single', boundary: 1.0, source: 'text_layer', confidence: 1.0 };
  store.pages = [{
    page_no: 1,
    url: '/page_1.png',
    width: 1000,
    height: 2000,
    layout: layout,
    column_boundary: layout.mode === 'double' ? layout.boundary : 1.0,
    snap_points: [],
    gap_candidates: [],
    column_snap_points: {},
    column_gap_candidates: {},
    manual_boxes: opts.manualBoxes || []
  }];
  store.records = opts.records || [
    { id: 11, page_no: 1, block_index: 0, block_x_start: 0, block_x_end: 1, block_y_start: 0.05, block_y_end: 0.30, column_index: 0, grad_status: 'unknown' }
  ];
  store.questionTypes = [];
  store.reasons = [];
  store.pageLayouts = {};
  store.layoutDirty = {};
  store.boxMode = false;
  store.manualBoxes = opts.manualBoxes ? { 1: opts.manualBoxes.map(function (b) { return b.slice(); }) } : {};
  store.boxDirty = {};
  store.boxDraft = null;
  store.boxSelected = null;
  // 选中态也要清：它跨用例泄漏时，后面的断言会因为页面停在上一次的聚焦态而意外失败
  store.selectedRecordId = null;
  store.boxSavedBody = {};
  store.boxSaveError = null;
  store.boxSaving = false;
  store.boxSaveQueued = false;
  // 上一段留下的自动保存计时器不能漏到这一段来（否则 runTimers 会把旧页的框提交一遍）
  timers.length = 0;
  detailPayload = null;
}

/** 触发一次完整重绘（顺带把 overlay 事件绑上）。 */
function render() {
  sandbox.setMistakeLayout('auto');       // → renderPageView
  sandbox.setMistakeCardFilter('all');    // → renderCards
}

function fireDocument(type, payload) {
  (documentHandlers[type] || []).slice().forEach(function (fn) { fn(payload); });
}

function pointerDown(target, x, y) {
  overlay.handlers.pointerdown.forEach(function (fn) {
    fn({
      target: { closest: function (selector) { return target[selector] || null; } },
      clientX: x, clientY: y, button: 0, preventDefault: function () {}
    });
  });
}

function pointerMove(x, y) { fireDocument('pointermove', { clientX: x, clientY: y }); }
function pointerUp() { fireDocument('pointerup', {}); }

/** 在空白处从 (x0,y0) 拖到 (x1,y1) 画一个框。 */
function drawBox(x0, y0, x1, y1) {
  pointerDown({}, x0, y0);
  pointerMove(x1, y1);
  pointerUp();
}

/** 抓住某个框的框体/把手拖到 (x,y)。 */
function dragHandle(selector, index, fromX, fromY, x, y) {
  const target = {};
  target[selector] = { dataset: selector === '[data-box-resize]' ? { boxResize: String(index) } : { box: String(index) } };
  pointerDown(target, fromX, fromY);
  pointerMove(x, y);
  pointerUp();
}

/** 单击某个框（不发 pointermove），走 click 处理器而不是拖拽。 */
function clickBox(index) {
  clearDragGuards();
  const node = { dataset: { box: String(index) } };
  overlay.handlers.click.forEach(function (fn) {
    fn({
      target: { closest: function (selector) { return selector === '[data-box]' ? node : null; } },
      clientX: 500, clientY: 500
    });
  });
}

/**
 * 合成 click 之前先清掉拖拽守卫。
 *
 * 本脚本是同步跑的：上一节 `drawBox` 走完 pointerup 会落下
 * `state.boxDragUntil = now + 300ms`，而真实时间根本没过去，于是这一节的 click 会被
 * 「拖完紧跟的 click」那道守卫整条吞掉，测出来的是假失败。节 [4b] 用的是同一招。
 */
function clearDragGuards() {
  store.boxDragUntil = 0;
  store.layoutDragUntil = 0;
}

/** 单击某个框上的编辑把手（✕ 删框 / 右下角改大小）。 */
function clickBoxHandle(which, index) {
  clearDragGuards();
  const node = { dataset: { box: String(index) } };
  const handle = which === 'remove'
    ? { dataset: { boxRemove: String(index) } }
    : { dataset: { boxResize: String(index) } };
  overlay.handlers.click.forEach(function (fn) {
    fn({
      target: {
        closest: function (selector) {
          if (selector === '[data-box]') return node;
          if (selector === '[data-box-remove],[data-box-resize]') return handle;
          return null;
        }
      },
      clientX: 500, clientY: 500
    });
  });
}

/** 单击某个自动块。 */
function clickRecord(recordId) {
  clearDragGuards();
  const node = { dataset: { record: String(recordId) } };
  overlay.handlers.click.forEach(function (fn) {
    fn({
      target: { closest: function (selector) { return selector === '[data-record]' ? node : null; } },
      clientX: 500, clientY: 500, metaKey: false, ctrlKey: false, preventDefault: function () {}
    });
  });
}

/** 单击页图空白（既不是块也不是框）。 */
function clickBlank() {
  clearDragGuards();
  overlay.handlers.click.forEach(function (fn) {
    fn({
      target: { closest: function () { return null; } },
      clientX: 500, clientY: 500
    });
  });
}

/** 某张卡片当前的 class 串 —— 用来断言选中环挂在正确那张卡上。 */
function cardClass(recordId) {
  const found = new RegExp('class="([^"]*)" data-card="' + recordId + '"').exec(cardList.innerHTML);
  return found ? found[1] : '';
}

function lastRequest() { return requests[requests.length - 1]; }
function lastBody() {
  const last = lastRequest();
  if (!last) return null;
  return JSON.parse(last.options.body || '{}');
}

const tick = () => new Promise(function (resolve) { setImmediate(resolve); });

(async function main() {

// ---------------------------------------------------------------- 1. 纯区间运算

console.log('\n[1] survivingSpans：与后端 _subtract_manual_boxes 同一套规则');
let record = { block_y_start: 0.10, block_y_end: 0.50 };
check('没有框时原样保留一整段',
  JSON.stringify(math.survivingSpans(record, [])) === JSON.stringify([[0.10, 0.50]]));
check('框打在中间 → 上下两段',
  JSON.stringify(math.survivingSpans(record, [[0, 0.20, 1, 0.30]])) === JSON.stringify([[0.10, 0.20], [0.30, 0.50]]));
check('框包住整块 → 无残段',
  JSON.stringify(math.survivingSpans(record, [[0, 0.05, 1, 0.55]])) === JSON.stringify([]));
check('框只压住头部 → 只剩尾段',
  JSON.stringify(math.survivingSpans(record, [[0, 0.05, 1, 0.25]])) === JSON.stringify([[0.25, 0.50]]));
check('横向不重叠的框不参与切分（框在页外右侧）',
  JSON.stringify(math.survivingSpans(
    { block_y_start: 0.10, block_y_end: 0.50, block_x_start: 0, block_x_end: 0.4 },
    [[0.5, 0.20, 0.9, 0.30]]
  )) === JSON.stringify([[0.10, 0.50]]));
check('横向只擦到 0.002 以内不算压到（与后端 epsilon 一致）',
  JSON.stringify(math.survivingSpans(
    { block_y_start: 0.10, block_y_end: 0.50, block_x_start: 0, block_x_end: 0.4 },
    [[0.4015, 0.20, 0.9, 0.30]]
  )) === JSON.stringify([[0.10, 0.50]]));
check('残留不足 0.8% 页高的碎片丢弃（阈值与后端 MANUAL_FRAGMENT_MIN_HEIGHT 同值）',
  JSON.stringify(math.survivingSpans(record, [[0, 0.10, 1, 0.499]])) === JSON.stringify([]),
  JSON.stringify(math.survivingSpans(record, [[0, 0.10, 1, 0.499]])));
check('残段 0.75% → 仍丢弃（阈值下侧最近一格）',
  JSON.stringify(math.survivingSpans(record, [[0, 0.10, 1, 0.4925]])) === JSON.stringify([]),
  JSON.stringify(math.survivingSpans(record, [[0, 0.10, 1, 0.4925]])));
check('残段恰好 0.8% → 保留（阈值上侧最近一格）',
  JSON.stringify(math.survivingSpans(record, [[0, 0.10, 1, 0.492]])) === JSON.stringify([[0.492, 0.50]]),
  JSON.stringify(math.survivingSpans(record, [[0, 0.10, 1, 0.492]])));
check('残段 1% → 保留：真实的一小截内容不该被误杀',
  JSON.stringify(math.survivingSpans(record, [[0, 0.10, 1, 0.49]])) === JSON.stringify([[0.49, 0.50]]),
  JSON.stringify(math.survivingSpans(record, [[0, 0.10, 1, 0.49]])));
check('两个框上下夹击 → 只剩三段（框各切走一条）',
  JSON.stringify(math.survivingSpans(record, [[0, 0.15, 1, 0.20], [0, 0.40, 1, 0.45]]))
    === JSON.stringify([[0.10, 0.15], [0.20, 0.40], [0.45, 0.50]]),
  JSON.stringify(math.survivingSpans(record, [[0, 0.15, 1, 0.20], [0, 0.40, 1, 0.45]])));
check('重叠的两个框不会切出负数长度的段',
  JSON.stringify(math.survivingSpans(record, [[0, 0.20, 1, 0.40], [0, 0.25, 1, 0.35]])) === JSON.stringify([[0.10, 0.20], [0.40, 0.50]]));

console.log('\n[2] 坐标规整与最小尺寸');
check('反向拖出的框就地交换坐标',
  JSON.stringify(math.normalizeBox(0.6, 0.5, 0.2, 0.1)) === JSON.stringify([0.2, 0.1, 0.6, 0.5]));
check('越界坐标夹到页内',
  JSON.stringify(math.normalizeBox(-0.4, -1, 1.6, 2)) === JSON.stringify([0, 0, 1, 1]));
check('小于 0.8% 的框判为过小',
  math.boxTooSmall([0.1, 0.1, 0.105, 0.9]) === true);
check('刚好 0.8% 的框判为过小（含浮点边界 0.108-0.1 = 0.007999…）',
  math.boxTooSmall([0.1, 0.1, 0.108, 0.9]) === true);
check('略大于 0.8% 的框不算过小',
  math.boxTooSmall([0.1, 0.1, 0.109, 0.9]) === false);

console.log('\n[2b] 双栏页：框边吸附到分栏线');
const doubleLayout = { isDouble: true, boundary: 0.4925 };
check('左边界溢出 1.05% → 吸到分栏线（右栏框不再吃掉左栏）',
  JSON.stringify(math.snapBoxToColumn([0.482, 0.1, 1, 0.5], doubleLayout)) === JSON.stringify([0.4925, 0.1, 1, 0.5]));
check('右边界差 1% 内 → 吸到分栏线（左栏框不再溢出到右栏）',
  JSON.stringify(math.snapBoxToColumn([0, 0.1, 0.5, 0.5], doubleLayout)) === JSON.stringify([0, 0.1, 0.4925, 0.5]));
check('故意跨栏的框不被吸走',
  JSON.stringify(math.snapBoxToColumn([0.3, 0.1, 0.7, 0.5], doubleLayout)) === JSON.stringify([0.3, 0.1, 0.7, 0.5]));
check('离分栏线超过容差的框不吸附',
  JSON.stringify(math.snapBoxToColumn([0.465, 0.1, 1, 0.5], doubleLayout)) === JSON.stringify([0.465, 0.1, 1, 0.5]));
check('单栏页不做吸附',
  JSON.stringify(math.snapBoxToColumn([0.482, 0.1, 1, 0.5], { isDouble: false })) === JSON.stringify([0.482, 0.1, 1, 0.5]));
check('吸附后的框确实不再碰到另一栏（横向重叠为 0）',
  (function () {
    const snapped = math.snapBoxToColumn([0.482, 0.1, 1, 0.5], doubleLayout);
    return Math.min(snapped[2], 0.4925) - Math.max(snapped[0], 0) <= 0;
  })());

// ---------------------------------------------------------------- 3. 画框

console.log('\n[3] 画框：拖动落框、尺寸判定、页内夹取');
loadPage();
render();
sandbox.toggleMistakeBoxMode();                         // 进入画框模式（loadPage 会把它关掉）
check('画框模式下自动块不接事件（否则起手就被抢走）',
  overlay.innerHTML.indexOf('pointer-events:none;') >= 0);
check('画框模式下分栏线也不接事件', overlay.innerHTML.indexOf('cursor:col-resize;pointer-events:none;') >= 0
  || overlay.innerHTML.indexOf('data-layout-handle') < 0);   // 单栏页本来就没有分栏线
drawBox(100, 200, 600, 800);                            // → [0.1, 0.1, 0.6, 0.4]
check('框已记入本页', JSON.stringify(store.manualBoxes[1]) === JSON.stringify([[0.1, 0.1, 0.6, 0.4]]),
  JSON.stringify(store.manualBoxes));
check('框标记为待应用', store.boxDirty[1] === true);
check('新画的框自动选中', store.boxSelected === 0);
check('草框已清掉', store.boxDraft === null);

drawBox(100, 100, 103, 103);                            // 3px × 3px，太小
check('过小的手抖框被丢掉', store.manualBoxes[1].length === 1, JSON.stringify(store.manualBoxes));

drawBox(900, 1800, 1400, 2400);                         // 拖到页外
check('越界部分被夹到页内', JSON.stringify(store.manualBoxes[1][1]) === JSON.stringify([0.9, 0.9, 1, 1]),
  JSON.stringify(store.manualBoxes[1][1]));

drawBox(700, 1600, 300, 1200);                          // 反向拖
check('反向拖出的框坐标被纠正', JSON.stringify(store.manualBoxes[1][2]) === JSON.stringify([0.3, 0.6, 0.7, 0.8]),
  JSON.stringify(store.manualBoxes[1][2]));

// ---------------------------------------------------------------- 4. 移动 / 改大小 / 删除

console.log('\n[4] 移动、改大小、删除');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
drawBox(100, 200, 600, 800);                            // [0.1, 0.1, 0.6, 0.4]
dragHandle('[data-box]', 0, 300, 500, 400, 700);        // 位移 (+0.1, +0.1)
check('整体移动：尺寸不变、位置平移',
  JSON.stringify(store.manualBoxes[1][0].map(function (v) { return Number(v.toFixed(4)); })) === JSON.stringify([0.2, 0.2, 0.7, 0.5]),
  JSON.stringify(store.manualBoxes[1][0]));

dragHandle('[data-box]', 0, 500, 500, -200, -200);      // 往页外拖
check('移动时框不越界（左上夹到 0）',
  JSON.stringify(store.manualBoxes[1][0].map(function (v) { return Number(v.toFixed(4)); })) === JSON.stringify([0, 0, 0.5, 0.3]),
  JSON.stringify(store.manualBoxes[1][0]));

dragHandle('[data-box-resize]', 0, 500, 300, 900, 700);
check('改大小：右下角跟着走',
  JSON.stringify(store.manualBoxes[1][0].map(function (v) { return Number(v.toFixed(4)); })) === JSON.stringify([0, 0, 0.9, 0.35]),
  JSON.stringify(store.manualBoxes[1][0]));

pointerDown({ '[data-box-remove]': { dataset: { boxRemove: '0' } } }, 500, 500);
check('删除把框从本页摘掉', store.manualBoxes[1].length === 0, JSON.stringify(store.manualBoxes));
check('删除后仍标记待应用', store.boxDirty[1] === true);

console.log('\n[4b] 单击框体（未拖动）要能选中，不能被拖拽守卫吞掉');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
drawBox(100, 200, 600, 800);
store.boxSelected = null;
store.boxDragUntil = 0;
pointerDown({ '[data-box]': { dataset: { box: '0' } } }, 300, 500);
pointerUp();                                            // 一次都没 pointermove
check('未拖动的手势不设守卫（否则 click 会被吞）', store.boxDragUntil === 0, String(store.boxDragUntil));
clickBox(0);
check('单击框体后被选中', store.boxSelected === 0, String(store.boxSelected));
check('单击不会误标脏', store.boxDirty[1] === undefined || store.boxDirty[1] === true);

console.log('\n[4c] 手势被打断时不留半截草框');
store.boxDraft = [0.1, 0.1, 0.5, 0.5];
pointerDown({}, 100, 100);
pointerMove(600, 700);
check('拖动中草框已出现', store.boxDraft !== null);
fireDocument('pointercancel', {});
check('打断后草框被丢掉', store.boxDraft === null, JSON.stringify(store.boxDraft));
check('打断不会把草框落成真框', store.manualBoxes[1].length === 1, JSON.stringify(store.manualBoxes));

// ---------------------------------------------------------------- 5. 请求体口径

console.log('\n[5] 请求体：boxes 数组 / 空数组＝清空');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
drawBox(100, 200, 600, 800);
resetRequests();
sandbox.applyMistakePageBoxes();
let body = lastBody();
check('打到本页框选接口',
  lastRequest().url === '/api/mistakes/batches/7/pages/1/blocks', lastRequest().url);
check('方法为 POST', lastRequest().options.method === 'POST');
check('框按 [[x0,y0,x1,y1]] 原样发出',
  JSON.stringify(body.boxes) === JSON.stringify([[0.1, 0.1, 0.6, 0.4]]), JSON.stringify(body));
check('请求体只有 boxes 一个键', Object.keys(body).length === 1, JSON.stringify(body));

await tick();
check('保存成功后仍留在画框模式（自动保存不该把人踢出模式）', store.boxMode === true);
check('保存成功后清掉未保存标记', store.boxDirty[1] === undefined);
check('保存成功后重新拉一次批次详情',
  requests.some(function (r) { return r.url === '/api/mistakes/batches/7'; }),
  JSON.stringify(requests.map(function (r) { return r.url; })));

console.log('\n[6] 清空本页框：空数组必须原样送出去');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
drawBox(100, 200, 600, 800);
drawBox(100, 1200, 600, 1600);
sandbox.clearMistakePageBoxes();
check('本地列表已清空', JSON.stringify(store.manualBoxes[1]) === '[]', JSON.stringify(store.manualBoxes));
check('清空后标记待应用', store.boxDirty[1] === true);
resetRequests();
sandbox.applyMistakePageBoxes();
body = lastBody();
check('清空发出的是空数组而不是跳过请求',
  Array.isArray(body.boxes) && body.boxes.length === 0, JSON.stringify(body));

await tick();

console.log('\n[7] 全是过小的框时先拦住，别静默清空');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
store.manualBoxes[1] = [[0.1, 0.1, 0.103, 0.9]];        // 绕过 UI 直接塞一个小框
store.boxDirty[1] = true;
resetRequests();
sandbox.applyMistakePageBoxes();
check('没有发出请求', requests.length === 0, JSON.stringify(requests.map(function (r) { return r.url; })));
check('框仍在本地（没被静默清掉）', store.manualBoxes[1].length === 1);

console.log('\n[8] 后端拒绝时保持原状，可重试');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
drawBox(100, 200, 600, 800);
failNext = true;
resetRequests();
sandbox.applyMistakePageBoxes();
await tick();
check('失败后仍标记待应用（不假装成功）', store.boxDirty[1] === true);
check('失败后框还在', JSON.stringify(store.manualBoxes[1]) === JSON.stringify([[0.1, 0.1, 0.6, 0.4]]),
  JSON.stringify(store.manualBoxes));
check('失败后没有退出画框模式', store.boxMode === true);
check('失败时不发批次详情请求（避免把本地框冲掉）',
  !requests.some(function (r) { return r.url === '/api/mistakes/batches/7'; }),
  JSON.stringify(requests.map(function (r) { return r.url; })));

// ---------------------------------------------------------------- 9. 画框开关

console.log('\n[9] 画框开关');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
check('点一下进画框模式', store.boxMode === true);
sandbox.toggleMistakeBoxMode();
check('再点一下退出画框模式', store.boxMode === false);

// ---------------------------------------------------------------- 10. 渲染口径

console.log('\n[10] 页图渲染：残段预览 / 整块被吃掉 / 操作条');
loadPage({
  records: [
    { id: 11, page_no: 1, block_index: 0, block_x_start: 0, block_x_end: 1, block_y_start: 0.05, block_y_end: 0.30, column_index: 0, grad_status: 'incorrect' },
    { id: 12, page_no: 1, block_index: 1, block_x_start: 0, block_x_end: 1, block_y_start: 0.40, block_y_end: 0.50, column_index: 0, grad_status: 'unknown' }
  ]
});
render();
check('无框时零变化：一块一段、没有虚线描边',
  overlay.innerHTML.indexOf('top:5%;height:25%') >= 0 && overlay.innerHTML.indexOf('outline:1px dashed') < 0);

store.manualBoxes[1] = [[0, 0.15, 1, 0.25], [0, 0.35, 1, 0.55]];
store.boxSelected = null;
render();
check('被框压住半截的块渲染成上下两段',
  overlay.innerHTML.indexOf('top:5%;height:10%') >= 0 && overlay.innerHTML.indexOf('top:25%;height:5%') >= 0,
  overlay.innerHTML.slice(0, 400));
check('残段带虚线描边（提示它被切过）', overlay.innerHTML.indexOf('outline:1px dashed #ef4444') >= 0);
check('整块被框吃掉 → 该块不再出现', overlay.innerHTML.indexOf('data-record="12"') < 0);
check('原块 title 说明剩下几段', overlay.innerHTML.indexOf('原块剩 2 段') >= 0);
check('框本身画出来了', overlay.innerHTML.indexOf('data-box="0"') >= 0 && overlay.innerHTML.indexOf('data-box="1"') >= 0);
check('框带删除把手与改大小把手',
  overlay.innerHTML.indexOf('data-box-remove="0"') >= 0 && overlay.innerHTML.indexOf('data-box-resize="0"') >= 0);
check('框角标显示尺寸', overlay.innerHTML.indexOf('框 1 · 100×10%') >= 0, overlay.innerHTML.slice(0, 600));

sandbox.toggleMistakeBoxMode();
check('画框模式显示操作条', elementFor('mistakeBoxBar').classList.contains('hidden') === false);
check('手动注入的框（等同服务端回显）算「已保存」',
  elementFor('mistakeBoxCount').textContent === '2 个框'
  && elementFor('mistakeBoxStatus').textContent === '已保存',
  elementFor('mistakeBoxCount').textContent + ' / ' + elementFor('mistakeBoxStatus').textContent);
drawBox(100, 1000, 300, 1200);
check('新画一个框后操作条改为「未保存」',
  elementFor('mistakeBoxCount').textContent === '3 个框'
  && elementFor('mistakeBoxStatus').textContent.indexOf('未保存') >= 0,
  elementFor('mistakeBoxStatus').textContent);
sandbox.toggleMistakeBoxMode();
check('退出画框模式隐藏操作条', elementFor('mistakeBoxBar').classList.contains('hidden') === true);

// ---------------------------------------------------------------- 10b. 选中标识

console.log('\n[10b] 选中块在页图上的标识：琥珀实线 + z 压过人工框 + 页内块号角标');
// 用户报的场景：不退出画框，在右侧点题块，页图上却看不出选中了哪一个 ——
// 人工框是蓝虚线、画在最上层，选中块原为蓝色内环，同色系又被盖住。
store.selectedRecordId = null;
store.boxSelected = null;
store.manualBoxes[1] = [];
render();
check('未选中时不出现琥珀描边与「当前」角标',
  overlay.innerHTML.indexOf('#f59e0b') < 0 && overlay.innerHTML.indexOf('当前 · 块') < 0);

// 情形一：块被框切剩一段，那一段还会画在页图上 —— 选中它时该标的是「块」
store.manualBoxes[1] = [[0, 0.15, 1, 0.30]];      // 压住 record 11 的下半，剩 0.05–0.15
store.selectedRecordId = 11;
render();
check('残段块被选中：琥珀色实线外扩 3px',
  overlay.innerHTML.indexOf('box-shadow:0 0 0 3px #f59e0b, inset 0 0 0 2px #f59e0b') >= 0,
  overlay.innerHTML.slice(0, 400));
check('旧的蓝色内环已被取代（蓝不再表示「选中」）',
  overlay.innerHTML.indexOf('inset 0 0 0 2px #2563eb') < 0);
check('残段块被选中：抬到 z-index:40，压过画在最上层的 z-30 人工框',
  overlay.innerHTML.indexOf('z-index:40') >= 0);
check('残段块被选中：角标写页内块号，与右侧卡片的「第N页 块M」对得上',
  overlay.innerHTML.indexOf('当前 · 块1') >= 0, overlay.innerHTML.slice(0, 400));

// 情形二（真机第 9 页的主流情形）：框与记录矩形重合 → 记录被自己的框整个吃掉，
// 页图上根本不画块。此时必须把**框**标出来，否则点完卡片页图上毫无反应。
store.manualBoxes[1] = [[0, 0.05, 1, 0.30]];
render();
check('框与选中记录重合：记录不再画块（被自己的框整个吃掉）',
  overlay.innerHTML.indexOf('data-record="11"') < 0, overlay.innerHTML.slice(0, 300));
check('框与选中记录重合：那个框换成琥珀实线 + 外发光',
  overlay.innerHTML.indexOf('border:2px solid #d97706') >= 0
  && overlay.innerHTML.indexOf('box-shadow:0 0 0 3px rgba(217,119,6,0.45)') >= 0,
  overlay.innerHTML.slice(0, 500));
check('框与选中记录重合：角标改写为「当前 · 块1」，不再只是「框 1 · …」',
  overlay.innerHTML.indexOf('当前 · 块1') >= 0 && overlay.innerHTML.indexOf('框 1 · ') < 0,
  overlay.innerHTML.slice(0, 500));
sandbox.toggleMistakeBoxMode();
check('画框模式下同样看得见（块是 pointer-events:none，只挡视觉不挡手势）',
  overlay.innerHTML.indexOf('border:2px solid #d97706') >= 0 || overlay.innerHTML.indexOf('当前 · 块1') >= 0,
  overlay.innerHTML.slice(0, 500));
sandbox.toggleMistakeBoxMode();
check('画框模式下的框仍然是可拖动的（聚焦不夺走把手事件）',
  overlay.innerHTML.indexOf('data-box-remove="0"') >= 0
  && overlay.innerHTML.indexOf('data-box-resize="0"') >= 0);

// 没被选中的框保持蓝虚线，避免「到处都在高亮」
store.manualBoxes[1] = [[0, 0.05, 1, 0.30], [0, 0.40, 1, 0.60]];
render();
check('只有当前那一个框变琥珀，别的框仍是蓝虚线',
  overlay.innerHTML.indexOf('border:2px dashed #3b82f6') >= 0
  && overlay.innerHTML.split('border:2px solid #d97706').length === 2,
  overlay.innerHTML.slice(0, 600));

store.selectedRecordId = null;
store.manualBoxes[1] = [];
render();
check('取消选中后琥珀描边与角标一起撤掉',
  overlay.innerHTML.indexOf('#f59e0b') < 0 && overlay.innerHTML.indexOf('#d97706') < 0
  && overlay.innerHTML.indexOf('当前 · 块') < 0,
  overlay.innerHTML.slice(0, 400));

// ---------------------------------------------------------------- 11. 右侧实时预览

console.log('\n[11] 右侧实时预览卡片（CSS 裁剪页图）');
loadPage();
render();
check('没改过时不出现待应用预览', cardList.innerHTML.indexOf('data-box-preview') < 0);
sandbox.toggleMistakeBoxMode();
drawBox(100, 200, 500, 600);                            // [0.1, 0.1, 0.5, 0.3]（rect 是 1000×2000）
check('右侧实时出现预览卡片', cardList.innerHTML.indexOf('data-box-preview="0"') >= 0);
check('预览卡片标了「待保存」', cardList.innerHTML.indexOf('待保存') >= 0);
check('预览卡片用了页图裁剪（86px 见方的裁剪窗）',
  cardList.innerHTML.indexOf('width:86px;height:86px') >= 0, cardList.innerHTML.slice(0, 500));
check('裁剪偏移按页尺寸折算（左 -21.5px / 上 -43px）',
  cardList.innerHTML.indexOf('margin-left:-21.50px') >= 0 && cardList.innerHTML.indexOf('margin-top:-43.00px') >= 0,
  cardList.innerHTML.slice(0, 700));
check('裁剪图尺寸＝页图按 0.215 缩放（215×430）',
  cardList.innerHTML.indexOf('width:215.00px;height:430.00px') >= 0, cardList.innerHTML.slice(0, 700));
check('裁剪图必须 max-width:none，否则会被 preflight 压成 86px 宽',
  cardList.innerHTML.indexOf('max-width:none;') >= 0);
check('卡片上能直接删框', cardList.innerHTML.indexOf('onclick="removeMistakeBox(0)"') >= 0);
check('顶部计数标出未保存的框数',
  elementFor('mistakeCardCount').textContent.indexOf('未保存 1 框') >= 0,
  elementFor('mistakeCardCount').textContent);

// ---------------------------------------------------------------- 12. 后端回显

console.log('\n[12] 服务端回显：已应用的框要回到本地、且不算待应用');
loadPage({ manualBoxes: [[0.2, 0.3, 0.8, 0.5]] });
render();
// 让「取批次详情」返回一份真实形状的 payload，走真实的 applyBatchDetail
detailPayload = {
  batch: { id: 7, subject: 'physics', title: '测试卷', page_count: 1, total: 1, status: 'reviewing' },
  records: [{ id: 11, page_no: 1, block_index: 0, block_x_start: 0.2, block_x_end: 0.8, block_y_start: 0.3, block_y_end: 0.5, column_index: 0, grad_status: 'unknown' }],
  pages: [{
    page_no: 1, url: '/page_1.png', width: 1000, height: 2000,
    layout: { mode: 'single', boundary: 1.0, source: 'text_layer', confidence: 1.0 },
    column_boundary: 1.0, manual_boxes: [[0.2, 0.3, 0.8, 0.5]]
  }],
  question_types: [], mistake_reasons: []
};
requests.length = 0;
sandbox.openMistakeBatch(7);
await tick();
check('回显的框进了本地', JSON.stringify(store.manualBoxes[1]) === JSON.stringify([[0.2, 0.3, 0.8, 0.5]]),
  JSON.stringify(store.manualBoxes));
check('回显的框不算待应用（不再显示预览卡片）',
  store.boxDirty[1] === undefined && cardList.innerHTML.indexOf('data-box-preview') < 0,
  cardList.innerHTML.slice(0, 300));
check('回显的框在页图上画出来了', overlay.innerHTML.indexOf('data-box="0"') >= 0);

console.log('\n[13] 后台刷新（整批重切完成后）不会抹掉未应用的框');
sandbox.toggleMistakeBoxMode();
drawBox(100, 100, 700, 900);                            // 新增一个框 → 标脏
const before = JSON.stringify(store.manualBoxes[1]);
check('画完确实是两个框', JSON.parse(before).length === 2, before);
resetRequests();
sandbox.startMistakeCut();                              // 重切 → onDone → 刷新详情
await tick();
await tick();
check('重切确实触发了批次详情刷新',
  requests.some(function (r) { return r.url === '/api/mistakes/batches/7'; }),
  JSON.stringify(requests.map(function (r) { return r.url; })));
check('待应用的框没被回显覆盖', JSON.stringify(store.manualBoxes[1]) === before, JSON.stringify(store.manualBoxes));
check('仍然标记待应用', store.boxDirty[1] === true);
check('重切清掉的是栏目的待重切标记，不是框',
  JSON.stringify(store.layoutDirty) === '{}');

console.log('\n[14] 双栏页画框：框边自动吸附，不会吃掉隔壁栏');
loadPage({
  layout: { mode: 'double', boundary: 0.4925, source: 'text_layer', confidence: 1.0 },
  records: [
    { id: 11, page_no: 1, block_index: 0, block_x_start: 0, block_x_end: 0.4925, block_y_start: 0.05, block_y_end: 0.30, column_index: 1, grad_status: 'incorrect' },
    { id: 12, page_no: 1, block_index: 1, block_x_start: 0.4925, block_x_end: 1, block_y_start: 0.05, block_y_end: 0.30, column_index: 2, grad_status: 'unknown' }
  ]
});
render();
sandbox.toggleMistakeBoxMode();
// 右栏框，但左边界手滑溢出到 0.482（肉眼看不出来，却足以把左栏切掉）
drawBox(482, 200, 1000, 800);
check('框的左边界被吸到分栏线 0.4925',
  Math.abs(store.manualBoxes[1][0][0] - 0.4925) < 1e-9, JSON.stringify(store.manualBoxes[1][0]));
check('吸附后推断为右栏', math.columnOfBox({ isDouble: true, boundary: 0.4925 }, store.manualBoxes[1][0]) === 2);
check('吸附后左栏题块不再被切（仍是完整一段）',
  overlay.innerHTML.indexOf('data-record="11"') >= 0
  && overlay.innerHTML.indexOf('原块剩') < 0,
  overlay.innerHTML.slice(0, 400));
check('左栏题块不带虚线残段描边', overlay.innerHTML.indexOf('outline:1px dashed') < 0);

console.log('\n[15] 栏目待重切时，拒绝应用框选');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
drawBox(100, 200, 600, 800);
store.layoutDirty[1] = true;
resetRequests();
sandbox.applyMistakePageBoxes();
check('栏目待重切 → 不发请求', requests.length === 0, JSON.stringify(requests.map(function (r) { return r.url; })));
check('框仍在本地（没被清掉）', store.manualBoxes[1].length === 1);
check('仍标记待应用', store.boxDirty[1] === true);
store.layoutDirty = {};
resetRequests();
sandbox.applyMistakePageBoxes();
check('重切后再点就能正常发出', requests.length === 1 && lastRequest().url.endsWith('/pages/1/blocks'),
  JSON.stringify(requests.map(function (r) { return r.url; })));
await tick();

// ---------------------------------------------------------------- 16. 静默自动保存

function lastBlockBody() { return JSON.parse(blockPosts()[blockPosts().length - 1].options.body); }

console.log('\n[16] 静默自动保存：停手 1 秒入库');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
resetRequests();
drawBox(100, 200, 600, 800);
check('画完不立刻发请求（是防抖，不是立即）', blockPosts().length === 0,
  JSON.stringify(requests.map(function (r) { return r.url; })));
check('排队了一个自动保存计时器', pendingTimerCount() === 1, String(pendingTimerCount()));
check('操作条显示「未保存」',
  elementFor('mistakeBoxStatus').textContent.indexOf('未保存') >= 0,
  elementFor('mistakeBoxStatus').textContent);
runTimers();
check('停手 1 秒后自动提交一次', blockPosts().length === 1,
  JSON.stringify(requests.map(function (r) { return r.url; })));
check('自动提交的框内容正确',
  JSON.stringify(lastBlockBody().boxes) === JSON.stringify([[0.1, 0.1, 0.6, 0.4]]),
  JSON.stringify(lastBlockBody()));
await tick();
check('入库成功后清掉未保存标记', store.boxDirty[1] === undefined);
check('入库成功后操作条回到「已保存」',
  elementFor('mistakeBoxStatus').textContent === '已保存',
  elementFor('mistakeBoxStatus').textContent);
check('入库成功后重新拉一次批次详情（换成真实块图）',
  requests.some(function (r) { return r.url === '/api/mistakes/batches/7'; }));

console.log('\n[17] 防抖：连续画框只提交一次');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
resetRequests();
drawBox(100, 200, 600, 800);
drawBox(100, 900, 600, 1100);
drawBox(100, 1300, 600, 1500);
check('三次画框只排一个计时器（后一个把前一个顶掉）',
  pendingTimerCount() === 1, String(pendingTimerCount()));
runTimers();
check('一次提交带上三个框', blockPosts().length === 1 && lastBlockBody().boxes.length === 3,
  String(blockPosts().length) + ' / ' + JSON.stringify(lastBlockBody()));
await tick();

console.log('\n[18] 幂等：改完又改回去，不再空跑一次');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
drawBox(100, 200, 600, 800);
runTimers();
await tick();
resetRequests();
store.boxDirty[1] = true;                  // 标脏，但内容与上次提交的一模一样
sandbox.applyMistakePageBoxes();
check('内容没变就不发请求', blockPosts().length === 0,
  JSON.stringify(requests.map(function (r) { return r.url; })));
check('但仍把未保存标记收掉（界面上不会一直挂着「未保存」）',
  store.boxDirty[1] === undefined);

console.log('\n[19] 串行：请求飞着时又改，回来补发而不是并发两个');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
resetRequests();
drawBox(100, 200, 600, 800);
sandbox.applyMistakePageBoxes();                       // 第一个请求出发（还没回来）
drawBox(100, 900, 600, 1100);                          // 飞行期间又画一个
sandbox.applyMistakePageBoxes();
check('并发时只会有一个请求在飞', blockPosts().length === 1,
  JSON.stringify(requests.map(function (r) { return r.url; })));
await tick();
await tick();
await tick();
check('第一个回来后补发了第二个', blockPosts().length === 2, String(blockPosts().length));
check('补发的那个带上了后画的框', lastBlockBody().boxes.length === 2, JSON.stringify(lastBlockBody()));

console.log('\n[20] 保存失败：框留着、原因写明、可重试');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
drawBox(100, 200, 600, 800);
resetRequests();
failNext = true;
sandbox.applyMistakePageBoxes();
await tick();
check('失败后框还在本地', store.manualBoxes[1].length === 1, JSON.stringify(store.manualBoxes));
check('失败后仍标未保存', store.boxDirty[1] === true);
check('失败原因写进操作条',
  elementFor('mistakeBoxStatus').textContent.indexOf('拒绝了这次请求') >= 0,
  elementFor('mistakeBoxStatus').textContent);
check('重试按钮出现',
  elementFor('mistakeBoxRetryBtn').classList.contains('hidden') === false);
resetRequests();
sandbox.retryMistakeBoxSave();
check('重试真的又发了一次', blockPosts().length === 1, JSON.stringify(requests.map(function (r) { return r.url; })));
await tick();
check('重试成功后清掉错误', store.boxSaveError === null);
check('重试成功后未保存标记也收掉', store.boxDirty[1] === undefined);

console.log('\n[21] 退出画框模式：攒着的框立刻送出，不随计时器丢掉');
loadPage();
render();
sandbox.toggleMistakeBoxMode();
resetRequests();
drawBox(100, 200, 600, 800);
check('还没到点，一个请求都没发', blockPosts().length === 0);
sandbox.toggleMistakeBoxMode();                        // 退出画框模式
check('退出时立刻提交，不等那 1 秒', blockPosts().length === 1,
  JSON.stringify(requests.map(function (r) { return r.url; })));
await tick();

console.log('\n[22] 翻页：先把本页的框送出去再换页');
loadPage();
const firstPage = store.pages[0];
store.pages = [firstPage, {
  page_no: 2, url: '/page_2.png', width: 1000, height: 2000,
  layout: { mode: 'single', boundary: 1.0, source: 'text_layer', confidence: 1.0 },
  column_boundary: 1.0, snap_points: [], gap_candidates: [],
  column_snap_points: {}, column_gap_candidates: {}, manual_boxes: []
}];
render();
sandbox.toggleMistakeBoxMode();
drawBox(100, 200, 600, 800);
resetRequests();
sandbox.mistakePageStep(1);
check('翻页前先把第 1 页的框提交掉', blockPosts().length === 1 && blockPosts()[0].url.indexOf('/pages/1/blocks') > 0,
  JSON.stringify(requests.map(function (r) { return r.url; })));
check('页确实翻了', store.pageIndex === 1);
await tick();

console.log('\n[23] 非画框模式下不排队自动保存');
loadPage();
render();
store.manualBoxes[1] = [[0.1, 0.1, 0.5, 0.3]];          // 等同服务端回显，本地有框但不在画框模式
store.boxMode = false;
resetRequests();
sandbox.clearMistakePageBoxes();
check('不在画框模式 → 什么都不发', blockPosts().length === 0,
  JSON.stringify(requests.map(function (r) { return r.url; })));
runTimers();                                            // 「等 1 秒」也不该发生任何事
check('把计时器跑完也没有请求（自动保存确实被模式挡住了）', blockPosts().length === 0,
  JSON.stringify(requests.map(function (r) { return r.url; })));
check('但未保存标记留着（重进画框模式后会被看见并补交）', store.boxDirty[1] === true);

// ---------------------------------------------------------------- 24. 非画框模式点框

console.log('\n[24] 非画框模式下点人工框 = 选中它对应的题块');

// 用户报的场景：左侧点画好的框，右侧毫无反应。根因是框生成的那条记录在页图上
// **不画块**（被自己的框整个吃掉），页图上唯一可触的区域就是这个框 —— 而点击
// 处理器只在画框模式下认 [data-box]，出了画框模式只认 [data-record]，于是这类
// 记录在页图上完全点不到。实测真机批次 107 条里 52 条是这种。

function boxLinkPage() {
  return loadPage({
    records: [
      { id: 11, page_no: 1, block_index: 0, block_x_start: 0, block_x_end: 1, block_y_start: 0.05, block_y_end: 0.30, column_index: 0, grad_status: 'unknown' },
      { id: 12, page_no: 1, block_index: 1, block_x_start: 0, block_x_end: 0.6, block_y_start: 0.40, block_y_end: 0.55, column_index: 0, grad_status: 'unknown' }
    ],
    manualBoxes: [[0, 0.40, 0.6, 0.55]]      // 与 id=12 矩形完全重合 → 那条记录被自己的框吃掉
  });
}

boxLinkPage();
render();
check('这条框对应的记录确实不画块（页图上只有框可点）',
  overlay.innerHTML.indexOf('data-record="12"') < 0, overlay.innerHTML.slice(0, 400));
check('非画框模式下框体光标是手型，不再是 move（move 会摆出「能拖」的假象）',
  overlay.innerHTML.indexOf('cursor:pointer') >= 0 && overlay.innerHTML.indexOf('cursor:move') < 0,
  overlay.innerHTML.slice(0, 300));
check('非画框模式下 title 写的是「点它＝选中右侧题块」',
  overlay.innerHTML.indexOf('点击＝选中右侧对应的题块') >= 0 && overlay.innerHTML.indexOf('拖动框体') < 0,
  overlay.innerHTML.slice(0, 400));

store.selectedRecordId = null;
boxLinkPage();
render();
check('点之前没有选中态',
  store.selectedRecordId === null && overlay.innerHTML.indexOf('当前 · 块') < 0);

clickBox(0);
check('点框 → 选中的正是框生成的那条记录', store.selectedRecordId === 12, String(store.selectedRecordId));
check('页图上该框换成琥珀实线 + 「当前 · 块2」',
  overlay.innerHTML.indexOf('border:2px solid #d97706') >= 0
  && overlay.innerHTML.indexOf('当前 · 块2') >= 0,
  overlay.innerHTML.slice(0, 600));
check('右侧 id=12 的卡片拿到选中环', cardClass(12).indexOf('ring-2 ring-brand-500') >= 0, cardClass(12));
check('右侧别的卡片没有环', cardClass(11).indexOf('ring-brand-500') < 0, cardClass(11));

sandbox.toggleMistakeBoxMode();
check('画框模式下框体光标回到 move（这是要拖的）',
  overlay.innerHTML.indexOf('cursor:move') >= 0, overlay.innerHTML.slice(0, 300));
check('画框模式下 title 回到编辑口径',
  overlay.innerHTML.indexOf('拖动框体可整体移动') >= 0, overlay.innerHTML.slice(0, 400));
store.selectedRecordId = null;
clickBox(0);
check('画框模式下点框只是选中框本身，不动右侧选中态',
  store.boxSelected === 0 && store.selectedRecordId === null, String(store.selectedRecordId));
sandbox.toggleMistakeBoxMode();

store.selectedRecordId = null;
render();
clickBoxHandle('remove', 0);
check('非画框模式下点 ✕ 不跳卡片（把手的活只在画框模式里干）',
  store.selectedRecordId === null, String(store.selectedRecordId));
clickBoxHandle('resize', 0);
check('非画框模式下点改大小把手同样不动选中态',
  store.selectedRecordId === null, String(store.selectedRecordId));

console.log('\n[24b] 只做精确匹配：匹配不到就不动，宁可没反应也不跳错地方');
loadPage({
  records: [
    { id: 21, page_no: 1, block_index: 0, block_x_start: 0, block_x_end: 1, block_y_start: 0.05, block_y_end: 0.30, column_index: 0, grad_status: 'unknown' }
  ],
  manualBoxes: [[0, 0.15, 1, 0.25]]        // 只压住半块，没有任何记录与它矩形相等
});
render();
store.selectedRecordId = null;
clickBox(0);
check('框只压住半块 → 不跳（残段的选中走块自己的通道）',
  store.selectedRecordId === null, String(store.selectedRecordId));

loadPage({
  records: [
    { id: 31, page_no: 1, block_index: 0, block_x_start: 0, block_x_end: 1, block_y_start: 0.10, block_y_end: 0.40, column_index: 0, grad_status: 'unknown' }
  ],
  manualBoxes: [[0, 0.10, 1, 0.20], [0, 0.25, 1, 0.40]]   // 人工合并后的记录，矩形是两块并集
});
render();
store.selectedRecordId = null;
clickBox(0);
check('合并记录的矩形是并集 → 单个框不认领（否则点哪个框都跳到同一条）',
  store.selectedRecordId === null, String(store.selectedRecordId));

loadPage({
  records: [
    { id: 41, page_no: 1, block_index: 0, block_x_start: 0, block_x_end: 1, block_y_start: 0.05, block_y_end: 0.30, column_index: 0, grad_status: 'unknown' },
    { id: 42, page_no: 1, block_index: 1, block_x_start: 0, block_x_end: 1, block_y_start: 0.40, block_y_end: 0.50, column_index: 0, grad_status: 'unknown' }
  ],
  manualBoxes: [[0, 0.15, 1, 0.25]]        // 压住 id=41 中间：41 剩两段、42 完整
});
render();
store.selectedRecordId = null;
clickRecord(41);
check('点自动块仍然选那条记录（新的框分支没抢走原有的块点击）',
  store.selectedRecordId === 41, String(store.selectedRecordId));
store.selectedRecordId = null;
clickBlank();
check('点页图空白不改变选中态', store.selectedRecordId === null, String(store.selectedRecordId));

console.log('\n' + '='.repeat(60));
if (fail === 0) {
  console.log('全部通过：' + pass + ' 项');
  process.exit(0);
}
console.log('失败 ' + fail + ' 项 / 共 ' + (pass + fail) + ' 项');
process.exit(1);

})();
