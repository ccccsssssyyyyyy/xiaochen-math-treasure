/**
 * 错题工作台「删除题块 / 撤销 / 卡片定位」端到端校验 —— 真实执行 mistake.js。
 *
 * 这一层最安静的错法是「删了又自己回来」：前端只发了个删除请求、把名单丢了，看起来
 * 当场对了，可用户下一次画框/合并触发的整页重建里，那块又原样冒出来。而界面上的
 * 表现是「删除无效」，用户只会以为按钮坏了。所以这里断言两件事：
 *   1) 每次删除发出去的是**全量**名单（旧名单 + 新删的那块），不是「就删这块」；
 *   2) 撤销发回去的是**操作前**的那一份名单 —— 撤销的实现就是再提交一次旧状态。
 * 另外覆盖：点卡片时左侧页图定位闪烁、判定按钮从三个变两个后的 toggle 语义。
 *
 * 用法: node tests/js/mistake_hidden_payload_check.js [mistake.js 路径]
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

// 假 DOM 里的异常会被 promise 链吞掉，只剩一句没头没尾的 FAIL。这里把它揪出来 ——
// 前端代码里的真 bug 大多就是这样消失得无影无踪的（界面上只表现为「什么都没发生」）。
process.on('unhandledRejection', function (err) {
  console.error('  [未处理的拒绝] ' + (err && err.stack ? err.stack : err));
  fail++;
});
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
      contains: function (c) { return classes.has(c); },
      // toggle 必须有：updateRecognizeButton 会用它，缺了就在 promise 链里抛 TypeError，
      // 表现成「状态更新了、后面的 toast 不见了」这种极难定位的半截现象。
      toggle: function (c, force) {
        const on = force === undefined ? !classes.has(c) : !!force;
        if (on) classes.add(c); else classes.delete(c);
        return on;
      }
    },
    setAttribute: function (name, value) { this.dataset['attr_' + name] = value; },
    getAttribute: function (name) { return this.dataset['attr_' + name]; },
    removeAttribute: function (name) { delete this.dataset['attr_' + name]; },
    addEventListener: function (type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); },
    querySelector: function () { return null; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: 1000, height: 2000 }; }
  };
}

const elements = {};
function elementFor(id) {
  if (!elements[id]) elements[id] = makeElement(id);
  return elements[id];
}

const requests = [];
const documentHandlers = {};
const timers = [];
let timerSeq = 1;
let toasts = [];
// 假后端的「已删名单」：键是页号。真实后端把它落盘到 analysis.json，这里只放内存。
let hiddenByPage = {};

function runTimers() {
  const queued = timers.splice(0, timers.length);
  queued.forEach(function (timer) { timer.fn(); });
}

function jsonResponse(ok, status, data) {
  return Promise.resolve({ ok: ok, status: status, json: function () { return Promise.resolve(data); } });
}

function hiddenPosts() {
  return requests.filter(function (r) { return /\/pages\/\d+\/hidden$/.test(r.url); });
}

function lastHiddenBody() {
  const posts = hiddenPosts();
  return posts.length ? JSON.parse(posts[posts.length - 1].options.body) : null;
}

function gradPuts() {
  return requests.filter(function (r) { return /\/api\/mistakes\/records\/\d+$/.test(r.url); });
}

/** 与后端同口径的命中判断：交叠占较小者、且占该块自身，两个都要 ≥ 0.6。 */
function isHidden(rect, other) {
  const width = Math.min(rect[2], other[2]) - Math.max(rect[0], other[0]);
  const height = Math.min(rect[3], other[3]) - Math.max(rect[1], other[1]);
  if (width <= 0 || height <= 0) return false;
  const overlap = width * height;
  const own = (other[2] - other[0]) * (other[3] - other[1]);
  const smallest = Math.min((rect[2] - rect[0]) * (rect[3] - rect[1]), own);
  if (smallest <= 0 || own <= 0) return false;
  return overlap / smallest >= 0.6 && overlap / own >= 0.6;
}

const BASE_RECORDS = [
  { id: 11, page_no: 1, block_index: 0, block_x_start: 0.0, block_x_end: 0.49, block_y_start: 0.05, block_y_end: 0.14, column_index: 1, grad_status: 'unknown', error_reason: '', include_in_handout: false, answer_source: '', answer_reviewed: false, image_block: '/b00.png', recognize_status: 'pending', merge_id: '', merged_block_count: 1, block_images: [], figure_images: [], answer_images: [] },
  { id: 12, page_no: 1, block_index: 1, block_x_start: 0.51, block_x_end: 1.0, block_y_start: 0.08, block_y_end: 0.17, column_index: 2, grad_status: 'unknown', error_reason: '', include_in_handout: false, answer_source: '', answer_reviewed: false, image_block: '/b01.png', recognize_status: 'pending', merge_id: '', merged_block_count: 1, block_images: [], figure_images: [], answer_images: [] },
  { id: 13, page_no: 1, block_index: 2, block_x_start: 0.0, block_x_end: 0.49, block_y_start: 0.60, block_y_end: 0.70, column_index: 1, grad_status: 'unknown', error_reason: '', include_in_handout: false, answer_source: '', answer_reviewed: false, image_block: '/b02.png', recognize_status: 'pending', merge_id: '', merged_block_count: 1, block_images: [], figure_images: [], answer_images: [] }
];

function rectOf(record) {
  return [record.block_x_start, record.block_y_start, record.block_x_end, record.block_y_end];
}

/** 假后端的「重建记录」：剔掉命中已删名单的块，其余原样。 */
function currentRecords() {
  const hidden = hiddenByPage[1] || [];
  return BASE_RECORDS.filter(function (record) {
    return !hidden.some(function (rect) { return isHidden(rect, rectOf(record)); });
  }).map(function (record) { return Object.assign({}, record); });
}

function echoDetail() {
  return {
    batch: { id: 7, subject: 'physics', title: '测试卷', page_count: 1 },
    records: currentRecords(),
    pages: [{
      page_no: 1, url: '/page_1.png', width: 1000, height: 2000,
      layout: { mode: 'double', boundary: 0.5, source: 'text_layer', confidence: 1.0 },
      column_boundary: 0.5, snap_points: [], gap_candidates: [],
      column_snap_points: {}, column_gap_candidates: {},
      manual_boxes: [], manual_merges: [],
      hidden_blocks: hiddenByPage[1] || []
    }],
    question_types: [], mistake_reasons: []
  };
}

const sandbox = {
  console: console,
  setTimeout: function (fn, delay) { const id = timerSeq++; timers.push({ id: id, fn: fn, delay: delay || 0 }); return id; },
  clearTimeout: function (id) { const i = timers.findIndex(function (t) { return t.id === id; }); if (i >= 0) timers.splice(i, 1); },
  setInterval: function () { return 0; },
  clearInterval: function () {},
  fetch: function (url, options) {
    requests.push({ url: url, options: options || {} });
    const opts = options || {};
    if (/\/pages\/\d+\/hidden$/.test(url)) {
      const rects = (JSON.parse(opts.body).rects || []);
      hiddenByPage[1] = rects;
      return jsonResponse(true, 200, { status: 'success', hidden_count: rects.length, block_count: currentRecords().length, removed: 3, hidden_blocks: rects });
    }
    if (/^\/api\/mistakes\/records\/\d+$/.test(url)) {
      const id = Number(url.split('/').pop());
      const body = JSON.parse(opts.body);
      const base = BASE_RECORDS.filter(function (r) { return r.id === id; })[0] || {};
      return jsonResponse(true, 200, { status: 'success', record: Object.assign({}, base, body) });
    }
    if (/^\/api\/mistakes\/batches\/\d+$/.test(url)) {
      return jsonResponse(true, 200, echoDetail());
    }
    if (/^\/api\/tasks\//.test(url)) {
      return jsonResponse(true, 200, { status: 'completed', block_count: 3, steps: [] });
    }
    return jsonResponse(true, 200, { status: 'success' });
  },
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  document: {
    getElementById: elementFor,
    addEventListener: function (type, fn) { (documentHandlers[type] = documentHandlers[type] || []).push(fn); },
    removeEventListener: function () {},
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
const cardList = elementFor('mistakeCardList');
const overlay = elementFor('mistakePageOverlay');
const toastMsg = elementFor('toastMessage');

function reset() {
  requests.length = 0;
  timers.length = 0;
  hiddenByPage = {};
  toasts = [];
  store.batch = { id: 7, subject: 'physics', title: '测试卷' };
  store.pageIndex = 0;
  store.pages = echoDetail().pages;
  store.records = currentRecords();
  store.questionTypes = [];
  store.reasons = [];
  store.selectedRecordId = null;
  store.mergePick = [];
  store.mergePending = null;
  store.undoStack = [];
  store.pageFlashId = null;
  store.boxMode = false;
  store.manualBoxes = {};
  store.boxDirty = {};
  store.cardFilter = 'all';
}

function fireDocument(type, payload) {
  (documentHandlers[type] || []).slice().forEach(function (fn) { fn(payload || {}); });
}
fireDocument('DOMContentLoaded');

function render() {
  elementFor('mistakeWorkspaceSection').classList.remove('hidden');
  elementFor('mistakeDetailView').classList.remove('hidden');
  sandbox.setMistakeLayout('auto');
  sandbox.setMistakeCardFilter('all');
}

function plainClickCard(recordId) {
  sandbox.handleMistakeCardClick({
    target: { closest: function () { return null; } },
    metaKey: false, ctrlKey: false, preventDefault: function () {}
  }, recordId);
}

function cmdClickCard(recordId) {
  sandbox.handleMistakeCardClick({
    target: { closest: function () { return null; } },
    metaKey: true, ctrlKey: false, preventDefault: function () {}
  }, recordId);
}

function pressKey(key, extra) {
  (documentHandlers.keydown || []).slice().forEach(function (fn) {
    fn(Object.assign({ key: key, target: { tagName: 'BODY' }, preventDefault: function () {} }, extra || {}));
  });
}

// 提交 → refreshMistakeDetail → render → toast，全在 promise 链上；假 fetch 每层还额外
// 套了 response.json()。用固定轮数的微任务「空转」去等它是猜轮数，猜少了就出现
// 「状态已更新、toast 还没写」的半截通过。setImmediate 的每一轮都会把微任务队列跑空，
// 于是链自己会走到底 —— 这里只保证「跑空两轮 + 中间放掉假定时器」。
function flush() {
  return new Promise(function (resolve) {
    setImmediate(function () {
      runTimers();
      setImmediate(resolve);
    });
  });
}

/** 取页图 overlay 里某个块的 HTML 片段（到该块的 </div> 为止）。 */
function blockHtml(recordId) {
  const html = String(overlay.innerHTML || '');
  const start = html.indexOf('data-record="' + recordId + '"');
  if (start < 0) return '';
  const end = html.indexOf('</div>', start);
  return html.slice(start, end < 0 ? html.length : end);
}

function hasCard(recordId) {
  return String(cardList.innerHTML || '').indexOf('data-card="' + recordId + '"') >= 0;
}

// ---------------------------------------------------------------- 用例

console.log('\n[1] 卡片上的按钮：删除 + 只有「对 / 错」两个判定');
reset();
render();
check('每张卡片有删除按钮', /fa-trash-can/.test(cardList.innerHTML) && /hideMistakeRecord\(11\)/.test(cardList.innerHTML));
check('勾选项文案是「进错题库」', cardList.innerHTML.indexOf('进错题库') >= 0);
check('卡片上不再出现「进错题本」', cardList.innerHTML.indexOf('进错题本') < 0);
check('判定按钮只剩对 / 错两个',
  /toggleMistakeGrad\(11,'correct'\)/.test(cardList.innerHTML) &&
  /toggleMistakeGrad\(11,'incorrect'\)/.test(cardList.innerHTML));
check('卡片上没有「未批」按钮', cardList.innerHTML.indexOf('未批') < 0, cardList.innerHTML.slice(0, 200));

console.log('\n[2] 删除：立刻从右侧消失，名单落盘，出现恢复入口');
reset();
render();
sandbox.hideMistakeRecord(11);
return flush().then(function () {
  check('发了一次删除请求', hiddenPosts().length === 1);
  const body = lastHiddenBody();
  check('提交的是该块矩形', JSON.stringify(body.rects) === '[[0,0.05,0.49,0.14]]', JSON.stringify(body));
  check('记录少了一条', store.records.length === 2, String(store.records.length));
  check('卡片消失', !hasCard(11));
  check('顶部出现「本页已删除 1 个题块」', cardList.innerHTML.indexOf('本页已删除 1 个题块') >= 0);
  check('并给出恢复入口', /restoreHiddenBlocks\(\)/.test(cardList.innerHTML));

  console.log('\n[3] 再次删除 → 提交的是「旧名单 + 新块」的全量，不是只删新的那块');
  sandbox.hideMistakeRecord(13);
  return flush();
}).then(function () {
  const body = lastHiddenBody();
  check('第二次提交带上了之前删过的矩形', body.rects.length === 2, JSON.stringify(body.rects));
  check('记录只剩一条', store.records.length === 1, String(store.records.length));
  check('计数升级为 2', cardList.innerHTML.indexOf('本页已删除 2 个题块') >= 0);

  console.log('\n[4] ⌘Z 撤销：再提交一次操作前的名单');
  pressKey('z', { metaKey: true });
  return flush();
}).then(function () {
  check('撤销又发了一次删除请求', hiddenPosts().length === 3);
  const body = lastHiddenBody();
  check('撤销提交的是上一步的名单（1 个矩形）', body.rects.length === 1, JSON.stringify(body.rects));
  check('撤销后记录回到 2 条', store.records.length === 2, String(store.records.length));
  check('卡片回来了', hasCard(13) && !hasCard(11));
  check('提示写明撤销了什么', /已撤销：删除/.test(toastMsg.textContent), JSON.stringify(toastMsg.textContent));

  console.log('\n[5] 继续 ⌘Z：把更早那次删除也撤掉；栈空了之后零请求');
  pressKey('Z', { metaKey: true });
  return flush().then(function () {
    check('第二块也恢复了', store.records.length === 3 && hasCard(11), String(store.records.length));
    const before = hiddenPosts().length;
    pressKey('z', { metaKey: true });
    return flush().then(function () {
      check('没有可撤销时零请求', hiddenPosts().length === before);
      check('提示「没有可撤销的操作」', toastMsg.textContent.indexOf('没有可撤销的操作') >= 0, toastMsg.textContent);
    });
  });
}).then(function () {
  console.log('\n[6] 全部恢复：提交空名单');
  sandbox.hideMistakeRecord(11);
  return flush();
}).then(function () {
  check('删除后只剩 2 条', store.records.length === 2, String(store.records.length));
  sandbox.restoreHiddenBlocks();
  return flush();
}).then(function () {
  const body = lastHiddenBody();
  check('恢复提交空数组', JSON.stringify(body.rects) === '[]', JSON.stringify(body));
  check('三块全回来了', store.records.length === 3, String(store.records.length));
  check('已删除提示条消失', cardList.innerHTML.indexOf('本页已删除') < 0);

  console.log('\n[7] 删除合并题 → 按外接矩形整组一起删');
  reset();
  store.records = [{
    id: 91, page_no: 1, block_index: 0, block_x_start: 0.0, block_x_end: 1.0,
    block_y_start: 0.05, block_y_end: 0.17, column_index: 0, grad_status: 'unknown',
    error_reason: '', include_in_handout: false, image_block: '/merged.png',
    recognize_status: 'pending', merge_id: 'm0000001', merged_block_count: 2,
    block_images: ['/b00.png', '/b01.png'], figure_images: [], answer_images: []
  }].concat(currentRecords().slice(2));
  render();
  sandbox.hideMistakeRecord(91);
  return flush();
}).then(function () {
  const body = lastHiddenBody();
  check('提交的是合并题的外接矩形', JSON.stringify(body.rects) === '[[0,0.05,1,0.17]]', JSON.stringify(body));

  console.log('\n[8] 点卡片 → 选中该题，左侧页图闪烁定位');
  reset();
  render();
  plainClickCard(12);
  check('选中了被点的那一题', store.selectedRecordId === 12, String(store.selectedRecordId));
  check('页图上该块出现闪烁层', blockHtml(12).indexOf('mistake-block-flash') >= 0, blockHtml(12));
  check('别的块不闪', blockHtml(11).indexOf('mistake-block-flash') < 0 && blockHtml(13).indexOf('mistake-block-flash') < 0);
  check('闪烁标记用完即清（不会满屏闪）', store.pageFlashId === null);
  render();
  check('普通重渲染不再闪', String(overlay.innerHTML).indexOf('mistake-block-flash') < 0);
  plainClickCard(13);
  check('点另一张卡片 → 选中跟着换', store.selectedRecordId === 13);
  check('闪的是新的那一块', blockHtml(13).indexOf('mistake-block-flash') >= 0 && blockHtml(12).indexOf('mistake-block-flash') < 0);

  console.log('\n[9] ⌘+点击卡片仍然只是多选，不夺走选中态');
  reset();
  render();
  cmdClickCard(11);
  check('进入多选', JSON.stringify(store.mergePick) === '[11]', JSON.stringify(store.mergePick));
  check('没有顺手把它设成选中', store.selectedRecordId === null);

  console.log('\n[10] 「对 / 错」按钮的 toggle：再点一次回到未批');
  reset();
  render();
  sandbox.toggleMistakeGrad(11, 'correct');
  return flush();
}).then(function () {
  const puts = gradPuts();
  check('第一次点「对」→ 提交 correct', puts.length === 1 && JSON.parse(puts[0].options.body).grad_status === 'correct', puts.length ? puts[0].options.body : '无请求');
  check('本地状态跟着变', store.records.filter(function (r) { return r.id === 11; })[0].grad_status === 'correct');
  sandbox.toggleMistakeGrad(11, 'correct');
  return flush();
}).then(function () {
  const puts = gradPuts();
  check('再点一次 → 提交 unknown（取消判定）', JSON.parse(puts[puts.length - 1].options.body).grad_status === 'unknown', puts[puts.length - 1].options.body);
  sandbox.toggleMistakeGrad(11, 'incorrect');
  return flush();
}).then(function () {
  const puts = gradPuts();
  check('点另一个 → 直接改成 incorrect', JSON.parse(puts[puts.length - 1].options.body).grad_status === 'incorrect');

  console.log('\n[11] 快捷键 3 仍然能清空判定（按钮没了，键盘保留等价入口）');
  reset();
  render();
  store.selectedRecordId = 11;
  store.records = store.records.map(function (r) {
    return r.id === 11 ? Object.assign({}, r, { grad_status: 'correct' }) : r;
  });
  pressKey('3');
  return flush();
}).then(function () {
  const puts = gradPuts();
  check('按 3 → 提交 unknown', puts.length === 1 && JSON.parse(puts[0].options.body).grad_status === 'unknown', puts.length ? puts[0].options.body : '无请求');

  console.log('\n' + '='.repeat(60));
  console.log('全部通过：' + pass + ' 项' + (fail ? '，失败 ' + fail + ' 项' : ''));
  process.exit(fail ? 1 : 0);
}).catch(function (err) {
  console.error('测试自身出错：', err);
  process.exit(1);
});
