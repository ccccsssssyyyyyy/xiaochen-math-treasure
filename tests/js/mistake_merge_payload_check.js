/**
 * 错题工作台「人工合并」端到端校验 —— 真实执行 mistake.js，不靠正则猜源码。
 *
 * 合并这一层的错法很安静：顺序点反了（题目上下颠倒，还看不出是哪里出的错）、
 * 状态冲突时静默丢了已填的错因、拆分提交的 merges 少了别的组（拆一个掉一个）。
 * 这些都不报错。所以这里用假 DOM 把 mistake.js 整个加载进 vm 沙箱，真实跑
 * 「⌘ 多选 → ⏎ 合并 → 冲突面板 → 拆分 / 调序 / 切方向」全链路，直接断言 store
 * 状态、卡片 HTML 与 fetch 请求体；末尾两组盯的是多选时的滚动行为（视野被拽回
 * 已选中块、多选条出现时列表位移、序号点击跳转）。
 *
 * 用法: node tests/js/mistake_merge_payload_check.js [mistake.js 路径]
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

/** 页码控件现在的样子：页号在输入框里，总页数在它右边。 */
function pageLabel() {
  return elementFor('mistakePageInput').value + ' ' + elementFor('mistakePageTotal').textContent;
}

function makeElement(id) {
  const classes = new Set();
  return {
    id: id,
    innerHTML: '',
    textContent: '',
    // 真实 DOM 的 input 一定有 value；假 DOM 缺了它，读 input.value 会拿到 undefined，
    // 页码这类「读自己写的值」的逻辑就成了「测试里永远走异常分支」
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
      // 与 column / box 两个用例保持一致：updateRecognizeButton 用的是 toggle，
      // 缺了它就在 promise 链里抛 TypeError，表现成「state 更新了、后续 toast 没了」
      // 这种查不出所以然的半截现象（异常被链吞掉，只剩一句没头没尾的 FAIL）。
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
let detailPayload = null;

function runTimers() {
  const queued = timers.splice(0, timers.length);
  queued.forEach(function (timer) { timer.fn(); });
}

function jsonResponse(ok, status, data) {
  return Promise.resolve({ ok: ok, status: status, json: function () { return Promise.resolve(data); } });
}

/** 最近一次 merges 提交的内容（假后端据此回显，模拟落盘后回读）。 */
function mergePosts() {
  return requests.filter(function (r) { return /\/pages\/\d+\/merges$/.test(r.url); });
}

function echoDetail() {
  const posts = mergePosts();
  let merges = [];
  if (posts.length) {
    merges = JSON.parse(posts[posts.length - 1].options.body).merges || [];
    // 后端会补 id：没有就编一个稳定的，前端靠它定位「拆哪一组」
    merges = merges.map(function (group, index) {
      return { id: group.id || ('m0000000' + index), rects: group.rects, direction: group.direction || 'v', primary: group.primary || 0 };
    });
  }
  return {
    batch: { id: 7, subject: 'physics', title: '测试卷', page_count: 1 },
    records: currentRecords(merges),
    pages: [{
      page_no: 1, url: '/page_1.png', width: 1000, height: 2000,
      layout: { mode: 'double', boundary: 0.5, source: 'text_layer', confidence: 1.0 },
      column_boundary: 0.5, snap_points: [], gap_candidates: [],
      column_snap_points: {}, column_gap_candidates: {},
      manual_boxes: [], manual_merges: merges
    }],
    question_types: [], mistake_reasons: []
  };
}

/**
 * 假后端的「重建记录」：把被合并的块换成一条记录，其余原样。只做前端需要的最小
 * 投影（块序号取成员最小、merge_id、merged_block_count），不做真正的拼图。
 */
function currentRecords(merges) {
  const base = [
    { id: 11, page_no: 1, block_index: 0, block_x_start: 0.0, block_x_end: 0.49, block_y_start: 0.05, block_y_end: 0.14, column_index: 1, grad_status: 'unknown', image_block: '/b00.png', recognize_status: 'pending', merge_id: '', merged_block_count: 1, block_images: [] },
    { id: 12, page_no: 1, block_index: 1, block_x_start: 0.51, block_x_end: 1.0, block_y_start: 0.08, block_y_end: 0.17, column_index: 2, grad_status: 'unknown', image_block: '/b01.png', recognize_status: 'pending', merge_id: '', merged_block_count: 1, block_images: [] },
    { id: 13, page_no: 1, block_index: 2, block_x_start: 0.0, block_x_end: 0.49, block_y_start: 0.60, block_y_end: 0.70, column_index: 1, grad_status: 'unknown', image_block: '/b02.png', recognize_status: 'pending', merge_id: '', merged_block_count: 1, block_images: [] }
  ];
  const used = new Set();
  const out = [];
  (merges || []).forEach(function (group) {
    const members = (group.rects || []).map(function (rect) {
      return base.filter(function (record) {
        return !used.has(record.id)
          && record.block_y_start < rect[3] && rect[1] < record.block_y_end
          && record.block_x_start < rect[2] && rect[0] < record.block_x_end;
      })[0];
    }).filter(Boolean);
    if (members.length < 2) return;
    members.forEach(function (record) { used.add(record.id); });
    out.push({
      id: 900 + out.length,
      page_no: 1,
      block_index: Math.min.apply(null, members.map(function (m) { return m.block_index; })),
      block_x_start: Math.min.apply(null, members.map(function (m) { return m.block_x_start; })),
      block_x_end: Math.max.apply(null, members.map(function (m) { return m.block_x_end; })),
      block_y_start: Math.min.apply(null, members.map(function (m) { return m.block_y_start; })),
      block_y_end: Math.max.apply(null, members.map(function (m) { return m.block_y_end; })),
      column_index: 0,
      grad_status: members[group.primary || 0].grad_status,
      error_reason: members[group.primary || 0].error_reason || '',
      recognize_status: 'pending',
      image_block: '/merged.png',
      merge_id: group.id,
      merged_block_count: members.length,
      block_images: members.map(function (m) { return m.image_block; })
    });
  });
  base.forEach(function (record) { if (!used.has(record.id)) out.push(Object.assign({}, record)); });
  out.sort(function (a, b) { return a.block_index - b.block_index; });
  return out;
}

const sandbox = {
  console: console,
  setTimeout: function (fn, delay) { const id = timerSeq++; timers.push({ id: id, fn: fn, delay: delay || 0 }); return id; },
  clearTimeout: function (id) { const i = timers.findIndex(function (t) { return t.id === id; }); if (i >= 0) timers.splice(i, 1); },
  setInterval: function () { return 0; },
  clearInterval: function () {},
  fetch: function (url, options) {
    requests.push({ url: url, options: options || {} });
    if (/^\/api\/mistakes\/batches\/\d+$/.test(url)) {
      return jsonResponse(true, 200, detailPayload || echoDetail());
    }
    if (/^\/api\/tasks\//.test(url)) {
      return jsonResponse(true, 200, { status: 'completed', block_count: 3, steps: [] });
    }
    return jsonResponse(true, 200, { status: 'success', merge_count: 1, block_count: 2, removed: 3, manual_merges: [] });
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
const dialog = elementFor('mistakeMergeDialog');

// 卡片列表的 querySelector 必须真能返回东西：本组用例要看两件事 —— 谁调了卡片的
// scrollIntoView（原来每次重建都顺手把「已选中块」拽回视野），以及重建时 scrollTop
// 有没有被保住。返回恒 null 的话断言会「全绿但什么都没测」。
const scrollCalls = [];
const MERGE_BAR_HEIGHT = 44;   // 多选条实际高度（px），用来验补偿算得对不对

cardList.querySelector = function (selector) {
  if (selector === '[data-merge-bar]') {
    return cardList.innerHTML.indexOf('data-merge-bar') >= 0
      ? { offsetHeight: MERGE_BAR_HEIGHT } : null;
  }
  const exact = /^\[data-card="(\d+)"\]$/.exec(selector);
  if (exact) {
    const id = exact[1];
    const present = cardList.innerHTML.indexOf('data-card="' + id + '"') >= 0
      || cardList.innerHTML.indexOf("data-card='" + id + "'") >= 0;
    if (!present) return null;
    return {
      offsetHeight: 200,
      scrollIntoView: function (opts) { scrollCalls.push({ id: Number(id), opts: opts || {} }); }
    };
  }
  return null;
};

/** 卡片列表上渲染出来的多选序号（按出现顺序）。 */
function jumpIds() {
  return (cardList.innerHTML.match(/data-merge-jump="\d+"/g) || [])
    .map(function (s) { return s.replace(/\D/g, ''); });
}

/** 卡片列表上渲染出来的卡片 id（按出现顺序）。 */
function cardIds() {
  return (cardList.innerHTML.match(/data-card="\d+"/g) || [])
    .map(function (s) { return Number(s.replace(/\D/g, '')); });
}

/** 跨页合并组的请求（批次级端点 —— 本页合并端点看不到它）。 */
function crossPosts() {
  return requests.filter(function (r) { return /\/cross-merges$/.test(r.url); });
}

/** 删除名单的请求。 */
function hiddenPosts() {
  return requests.filter(function (r) { return /\/pages\/\d+\/hidden$/.test(r.url); });
}

/**
 * 多跑几轮微任务与定时器。
 *
 * ``flush()`` 只够「一个 request → 一个 render」。跨页删除是「拆组 → 刷新详情 →
 * 逐页写隐藏名单」的多段链，轮数不够时断言会看到半截状态（而半截状态恰好长得
 * 像「功能没做」），是最容易写出假绿的地方。
 */
function flushDeep() {
  return flush().then(function () { return flush(); })
    .then(function () { return flush(); })
    .then(function () { return flush(); });
}

/** 造出第 2 页与它的两块 —— 跨页合并与翻页两组用例都要。 */
function addPage2() {
  store.pages.push({
    page_no: 2, url: '/page_2.png', width: 1000, height: 2000,
    layout: { mode: 'single', boundary: 1, source: 'text_layer', confidence: 1 },
    column_boundary: 1, snap_points: [], gap_candidates: [],
    column_snap_points: {}, column_gap_candidates: {},
    manual_boxes: [], manual_merges: []
  });
  store.records.push({
    id: 21, page_no: 2, block_index: 0, block_x_start: 0.01, block_x_end: 0.99,
    block_y_start: 0.05, block_y_end: 0.15, column_index: 0, grad_status: 'unknown',
    image_block: '/p2b0.png', recognize_status: 'pending',
    merge_id: '', merged_block_count: 1, block_images: []
  });
  store.records.push({
    id: 22, page_no: 2, block_index: 1, block_x_start: 0.01, block_x_end: 0.99,
    block_y_start: 0.20, block_y_end: 0.30, column_index: 0, grad_status: 'unknown',
    image_block: '/p2b1.png', recognize_status: 'pending',
    merge_id: '', merged_block_count: 1, block_images: []
  });
}

/** 把一个假的「跨页合并结果」摆进 store（模拟后端重建后的样子）。 */
function seedCrossMerge() {
  store.records = store.records.filter(function (r) { return r.id !== 11 && r.id !== 21; });
  store.records.push({
    id: 90, page_no: 1, block_index: 0, block_x_start: 0.0, block_x_end: 0.49,
    block_y_start: 0.05, block_y_end: 0.14, column_index: 1, grad_status: 'unknown',
    image_block: '/p001_mcross01.png', recognize_status: 'pending',
    merge_id: 'mcross01', merged_block_count: 2,
    block_images: ['/p001_b00.png', '/p002_b00.png']
  });
  store.crossPageMerges = [{
    id: 'mcross01', direction: 'v', primary: 0,
    members: [
      { page_no: 1, rect: [0.0, 0.05, 0.49, 0.14] },
      { page_no: 2, rect: [0.01, 0.05, 0.99, 0.15] }
    ]
  }];
}

/**
 * 真的「点」一下多选条上的序号：从渲染出来的 HTML 里取出 onclick 再执行。
 *
 * 不能直接调 jumpToMistakeMergePick 了事 —— 那样即使按钮忘了挂 onclick（点下去
 * 毫无反应，控制台也不报错），断言也照样全绿。走的必须是 HTML 里那条真实入口。
 */
function clickJumpChip(recordId) {
  const tag = new RegExp('<button[^>]*data-merge-jump="' + recordId + '"[^>]*>').exec(cardList.innerHTML);
  if (!tag) return false;
  const handler = /onclick="([^"]*)"/.exec(tag[0]);
  if (!handler) return false;
  vm.runInContext(handler[1], sandbox);
  return true;
}

function reset() {
  requests.length = 0;
  timers.length = 0;
  detailPayload = null;
  cardList.scrollTop = 0;
  scrollCalls.length = 0;
  store.batch = { id: 7, subject: 'physics', title: '测试卷' };
  store.pageIndex = 0;
  store.pages = [{
    page_no: 1, url: '/page_1.png', width: 1000, height: 2000,
    layout: { mode: 'double', boundary: 0.5, source: 'text_layer', confidence: 1.0 },
    column_boundary: 0.5, snap_points: [], gap_candidates: [],
    column_snap_points: {}, column_gap_candidates: {},
    manual_boxes: [], manual_merges: []
  }];
  store.records = currentRecords([]);
  store.questionTypes = [];
  store.reasons = [];
  store.selectedRecordId = null;
  store.mergePick = [];
  store.mergePending = null;
  store.crossPageMerges = [];
  // 撤销栈也要清：不清的话它会跨小节累积，让「这一步入栈了吗」这类断言变成
  // 一条永远为真的长列表（本次就踩了：断言 length === 1 却拿到 12 项）
  store.undoStack = [];
  store.boxMode = false;
  store.manualBoxes = {};
  store.boxDirty = {};
}

// 快捷键是在 DOMContentLoaded 里才绑的，测试必须自己触发一次，否则按什么都没反应
// （表现为「回车不提交」这种假通过）。
function fireDocument(type, payload) {
  (documentHandlers[type] || []).slice().forEach(function (fn) { fn(payload || {}); });
}
fireDocument('DOMContentLoaded');

function render() {
  // 快捷键有两个前置门禁：工作台区块与详情区块都得是「可见」的
  elementFor('mistakeWorkspaceSection').classList.remove('hidden');
  elementFor('mistakeDetailView').classList.remove('hidden');
  sandbox.setMistakeLayout('auto');
  sandbox.setMistakeCardFilter('all');
}

/** 普通点击卡片（＝选中这一题）。 */
function plainClickCard(recordId) {
  sandbox.handleMistakeCardClick({ target: { closest: function () { return null; } }, metaKey: false, ctrlKey: false, preventDefault: function () {} }, recordId);
}

/** ⌘ + 点击卡片（模拟 Mac 上的多选）。 */
function cmdClickCard(recordId) {
  sandbox.handleMistakeCardClick({ target: { closest: function () { return null; } }, metaKey: true, ctrlKey: false, preventDefault: function () {} }, recordId);
}

/** ⌘ + 点击页图上的块。 */
function cmdClickBlock(recordId) {
  overlay.handlers.click.forEach(function (fn) {
    fn({
      target: { closest: function (selector) { return selector === '[data-record]' ? { dataset: { record: String(recordId) } } : null; } },
      metaKey: true, ctrlKey: false, preventDefault: function () {}
    });
  });
}

function plainClickBlock(recordId) {
  overlay.handlers.click.forEach(function (fn) {
    fn({
      target: { closest: function (selector) { return selector === '[data-record]' ? { dataset: { record: String(recordId) } } : null; } },
      metaKey: false, ctrlKey: false, preventDefault: function () {}
    });
  });
}

function pressKey(key, extra) {
  (documentHandlers.keydown || []).slice().forEach(function (fn) {
    fn(Object.assign({ key: key, target: { tagName: 'BODY' }, preventDefault: function () {} }, extra || {}));
  });
}

function lastMergeBody() {
  const posts = mergePosts();
  return posts.length ? JSON.parse(posts[posts.length - 1].options.body) : null;
}

function flush() {
  // 提交 → refreshMistakeDetail → render，都是 promise 链；跑掉微任务与定时器
  return Promise.resolve().then(function () { return Promise.resolve(); })
    .then(function () { runTimers(); return Promise.resolve(); })
    .then(function () { return Promise.resolve(); });
}

// ---------------------------------------------------------------- 用例

console.log('\n[1] ⌘ + 点击：卡片与页图同步多选，序号＝拼接顺序');
reset();
render();
cmdClickCard(11);
check('点第一块 → 进入多选', store.mergePick.length === 1 && store.mergePick[0] === 11, JSON.stringify(store.mergePick));
cmdClickCard(12);
check('点第二块 → 顺序追加', JSON.stringify(store.mergePick) === '[11,12]', JSON.stringify(store.mergePick));
check('卡片上出现序号 ①②', /bg-violet-600[^>]*>1</.test(cardList.innerHTML) && /bg-violet-600[^>]*>2</.test(cardList.innerHTML));
cmdClickCard(11);
check('再点一次 → 移出多选', JSON.stringify(store.mergePick) === '[12]', JSON.stringify(store.mergePick));
cmdClickCard(11);
cmdClickBlock(13);
check('页图上 ⌘+点击同样生效', JSON.stringify(store.mergePick) === '[12,11,13]', JSON.stringify(store.mergePick));
check('操作条写明已选 3 块', /已选 3 块/.test(cardList.innerHTML), cardList.innerHTML.slice(0, 160));
plainClickBlock(12);
check('普通点击页图块仍然是「选中该题」，不动多选', store.selectedRecordId === 12 && store.mergePick.length === 3);

console.log('\n[2] Esc 清空、回车在不足两块时不提交');
reset();
render();
cmdClickCard(11);
pressKey('Enter');
check('只选一块时按回车不提交', mergePosts().length === 0);
pressKey('Escape');
check('Esc 清空多选', store.mergePick.length === 0);
check('清空后操作条消失', !/已选/.test(cardList.innerHTML));

console.log('\n[3] 状态一致 → 回车直接合并，不弹面板');
reset();
render();
cmdClickCard(11);
cmdClickCard(12);
pressKey('Enter');
check('直接发出合并请求', mergePosts().length === 1);
const body = lastMergeBody();
check('提交 1 个合并组', body && body.merges.length === 1);
check('rects 按点击顺序（先左栏后右栏）',
  JSON.stringify(body.merges[0].rects) === JSON.stringify([[0, 0.05, 0.49, 0.14], [0.51, 0.08, 1, 0.17]]),
  JSON.stringify(body.merges[0].rects));
check('没弹确认面板', dialog.classList.contains('hidden') && !store.mergePending);

console.log('\n[4] 状态冲突 → 弹面板，确认后按选中的 primary 提交');
reset();
store.records[1].grad_status = 'incorrect';
store.records[1].error_reason = '受力分析漏力';
render();
cmdClickCard(11);
cmdClickCard(12);
pressKey('Enter');
check('先不发请求，等确认', mergePosts().length === 0);
check('面板弹出来了', !dialog.classList.contains('hidden') && !!store.mergePending);
check('只列出冲突项（判定 / 错因）', /判定/.test(dialog.innerHTML) && /错因/.test(dialog.innerHTML) && !/识别结果/.test(dialog.innerHTML));
sandbox.pickMergePrimary(1);
check('切换 primary 后面板跟着高亮第 2 块', /以第 2 块为准/.test(dialog.innerHTML), dialog.innerHTML.slice(-200));
sandbox.confirmMistakeMerge();
const body2 = lastMergeBody();
check('确认后发出请求', !!body2);
check('primary = 1（以右栏那块为准）', body2.merges[0].primary === 1, JSON.stringify(body2.merges[0]));
check('确认后面板关闭', dialog.classList.contains('hidden') && !store.mergePending);

console.log('\n[5] 合并后的卡片：徽标 + 拆分 / 调序 / 切方向');
reset();
return flush().then(function () {
  detailPayload = null;
  // 先合并一次，让假后端把「已合并」的记录回显出来
  cmdClickCard(11);
  cmdClickCard(12);
  pressKey('Enter');
  return flush();
}).then(function () {
  const merged = store.records.filter(function (r) { return r.merge_id; })[0];
  check('出现一条合并记录', !!merged, JSON.stringify(store.records.map(function (r) { return r.merge_id; })));
  check('块数是 2', merged && merged.merged_block_count === 2);
  check('卡片标出「已合并 2 块」', /已合并 2 块/.test(cardList.innerHTML), cardList.innerHTML.slice(0, 300));
  check('卡片给出三个出口', /splitMistakeMerge/.test(cardList.innerHTML) && /flipMistakeMergeOrder/.test(cardList.innerHTML) && /toggleMistakeMergeDirection/.test(cardList.innerHTML));

  console.log('\n[6] 拆分：提交去掉该组的 merges（别的组不受影响）');
  const mergedId = merged.id;
  sandbox.splitMistakeMerge(mergedId);
  return flush();
}).then(function () {
  const body3 = lastMergeBody();
  check('拆分提交的是空列表（本页只剩这一组）', body3 && body3.merges.length === 0, JSON.stringify(body3));
  check('拆分后没有合并记录了', store.records.filter(function (r) { return r.merge_id; }).length === 0);
  check('记录回到 3 条', store.records.length === 3, String(store.records.length));

  console.log('\n[7] 调换顺序与切换方向');
  cmdClickCard(12);
  cmdClickCard(11);
  pressKey('Enter');
  return flush();
}).then(function () {
  const merged2 = store.records.filter(function (r) { return r.merge_id; })[0];
  check('合并成功', !!merged2);
  sandbox.flipMistakeMergeOrder(merged2.id);
  return flush();
}).then(function () {
  const body4 = lastMergeBody();
  // 提交时顺序是「先点的右栏 → 后点的左栏」，调换后应变成左栏在前
  check('rects 已反转', JSON.stringify(body4.merges[0].rects) === JSON.stringify([[0, 0.05, 0.49, 0.14], [0.51, 0.08, 1, 0.17]]), JSON.stringify(body4.merges[0].rects));
  check('primary 跟着镜像（0 → 1）', body4.merges[0].primary === 1, String(body4.merges[0].primary));
  const merged3 = store.records.filter(function (r) { return r.merge_id; })[0];
  sandbox.toggleMistakeMergeDirection(merged3.id);
  return flush();
}).then(function () {
  const body5 = lastMergeBody();
  check('方向切成 h（上下 → 左右）', body5.merges[0].direction === 'h', JSON.stringify(body5.merges[0]));

  console.log('\n[8] 跨页可合（走批次级端点）；已合并的块仍不让合');
  reset();
  addPage2();
  render();
  cmdClickCard(11);
  cmdClickCard(21);
  pressKey('Enter');
  // 「本页端点为 0」单独断言是没有判别力的（请求可能压根没发）。必须同时盯住
  // 批次级端点收到了一次 —— 否则这段在功能回退时会假绿。
  check('跨页合并只发批次级端点，本页合并端点一个请求都不发',
    mergePosts().length === 0 && crossPosts().length === 1,
    'merge=' + mergePosts().length + ' cross=' + crossPosts().length);
  const crossBody = JSON.parse(crossPosts()[0].options.body);
  check('成员带页号 —— 页内矩形是相对比例，跨页不可比，页号是唯一的归属依据',
    JSON.stringify(crossBody.cross_merges[0].members.map(function (m) { return m.page_no; })) === '[1,2]',
    JSON.stringify(crossBody.cross_merges[0].members));
  check('primary 指向第 1 页那一半（题干在上）', crossBody.cross_merges[0].primary === 0);
  check('矩形取的是卡片实际几何',
    JSON.stringify(crossBody.cross_merges[0].members[0].rect) === '[0,0.05,0.49,0.14]',
    JSON.stringify(crossBody.cross_merges[0].members[0].rect));

  reset();
  addPage2();
  render();
  cmdClickCard(21);            // 故意先点第 2 页那块
  cmdClickCard(11);
  pressKey('Enter');
  const flipped = JSON.parse(crossPosts()[0].options.body).cross_merges[0];
  check('点反了也按页码顺序归一（题干仍在上），不要求用户重来一遍',
    JSON.stringify(flipped.members.map(function (m) { return m.page_no; })) === '[1,2]',
    JSON.stringify(flipped.members.map(function (m) { return m.page_no; })));

  reset();
  render();
  cmdClickCard(11);
  cmdClickCard(12);
  pressKey('Enter');
  check('同页合并仍走本页端点（既有通道不被跨页改写）',
    mergePosts().length === 1 && crossPosts().length === 0,
    'merge=' + mergePosts().length + ' cross=' + crossPosts().length);

  reset();
  addPage2();
  store.records[0].merge_id = 'mold';
  store.records[0].merged_block_count = 2;
  render();
  cmdClickCard(11);
  cmdClickCard(21);
  pressKey('Enter');
  check('已合并的块仍不让合（不管在哪一页）',
    mergePosts().length === 0 && crossPosts().length === 0,
    'merge=' + mergePosts().length + ' cross=' + crossPosts().length);

  console.log('\n[9] 多选期间列表不再被拽回已选中块，滚动位置也钉住');
  reset();
  render();
  plainClickCard(11);          // 普通点击＝选中块 1，它自己那次滚动是预期行为
  scrollCalls.length = 0;
  cmdClickCard(12);
  cmdClickCard(13);
  check('⌘+点多选不再触发任何卡片滚动', scrollCalls.length === 0,
    JSON.stringify(scrollCalls.map(function (c) { return c.id; })));
  check('普通点击的选中不会混进多选', store.mergePick.indexOf(11) < 0
    && JSON.stringify(store.mergePick) === '[12,13]', JSON.stringify(store.mergePick));
  check('多选条上的序号仍是拼接顺序', jumpIds().join(',') === '12,13', jumpIds().join(','));

  reset();
  render();
  cardList.scrollTop = 500;
  cmdClickCard(11);
  check('多选条首次出现时补偿它占掉的高度（500 → 544）', cardList.scrollTop === 544,
    String(cardList.scrollTop));
  cmdClickCard(12);
  check('再加一块时滚动位置纹丝不动', cardList.scrollTop === 544, String(cardList.scrollTop));
  cmdClickCard(12);            // 再点一次＝把第 2 块移出多选
  check('取消一块时滚动位置同样不动', cardList.scrollTop === 544, String(cardList.scrollTop));
  pressKey('Escape');
  check('清空多选、条消失后位置回退 44px', cardList.scrollTop === 500, String(cardList.scrollTop));

  console.log('\n[10] 多选条上的序号可点击跳转');
  reset();
  render();
  cmdClickCard(11);
  cmdClickCard(12);
  check('序号渲染成可点按钮', jumpIds().join(',') === '11,12', jumpIds().join(','));
  check('序号按钮没顶掉多选条原有的出口',
    /requestMistakeMerge/.test(cardList.innerHTML) && /clearMistakeMergePick/.test(cardList.innerHTML));
  check('入口已导出到 window（否则点下去是 undefined）',
    typeof sandbox.jumpToMistakeMergePick === 'function');
  scrollCalls.length = 0;
  const clicked = clickJumpChip(12);
  check('序号按钮挂着真实点击入口（不是只画了个按钮）', clicked, cardList.innerHTML.slice(0, 260));
  check('点序号 → 滚到那一块并居中', scrollCalls.length === 1
    && scrollCalls[0].id === 12 && scrollCalls[0].opts.block === 'center',
    JSON.stringify(scrollCalls));
  sandbox.jumpToMistakeMergePick(999);
  check('目标块不在列表里时不炸也不滚', scrollCalls.length === 1, JSON.stringify(scrollCalls));

  console.log('\n[11] 翻页后右栏卡片必须跟着换页（页图与列表不能各说各话）');
  reset();
  addPage2();
  render();
  check('第 1 页时右栏只有本页的三块',
    cardIds().join(',') === '11,12,13', cardIds().join(','));
  cardList.scrollTop = 600;
  sandbox.mistakePageStep(1);
  check('翻页指示器前进到 2 / 2',
    pageLabel() === '2 / 2',
    pageLabel());
  check('右栏换成本页卡片，上一页的块一条都不留',
    cardIds().join(',') === '21,22', cardIds().join(','));
  check('翻页后列表回到顶部（不复用上一页的滚动位置）',
    cardList.scrollTop === 0, String(cardList.scrollTop));
  check('卡片计数也换成第 2 页',
    /第 2 页 · 2 题/.test(elementFor('mistakeCardCount').textContent),
    elementFor('mistakeCardCount').textContent);
  sandbox.mistakePageStep(-1);
  check('翻回第 1 页同样同步',
    cardIds().join(',') === '11,12,13' && cardList.scrollTop === 0,
    cardIds().join(',') + ' / ' + cardList.scrollTop);

  console.log('\n[12] 跨页合并的记录：标记 / 序号跳页 / 拆分 / 删除');

  reset();
  addPage2();
  seedCrossMerge();
  render();
  check('卡片标出「跨页合并 2 块」',
    /跨页合并 2 块 · 上下/.test(cardList.innerHTML), cardList.innerHTML.slice(0, 320));
  check('跨页题不给「改方向」按钮（两页横向坐标各自独立，左右拼没有意义）',
    cardList.innerHTML.indexOf('toggleMistakeMergeDirection') < 0);
  check('拆分 / 调换顺序两个出口还在',
    /splitMistakeMerge/.test(cardList.innerHTML) && /flipMistakeMergeOrder/.test(cardList.innerHTML));

  store.selectedRecordId = 90;
  sandbox.splitMistakeMerge(90);
  return flushDeep().then(function () {
    check('拆分跨页题走批次级端点',
      crossPosts().length === 1 && mergePosts().length === 0,
      'cross=' + crossPosts().length + ' merge=' + mergePosts().length);
    check('拆分后的请求体里那个组没了',
      JSON.parse(crossPosts()[0].options.body).cross_merges.length === 0,
      crossPosts()[0].options.body);

    console.log('\n[13] 跨页多选：序号带页号，且能跨页跳过去');
    reset();
    addPage2();
    render();
    cmdClickCard(11);
    cmdClickCard(21);
    check('跨页多选时序号带上页号（两页的块号会重复，不带就分不清）',
      /1页 块1/.test(cardList.innerHTML) && /2页 块1/.test(cardList.innerHTML),
      cardList.innerHTML.slice(0, 320));
    check('多选条标出「跨页」', /跨页<\/span>/.test(cardList.innerHTML)
      || cardList.innerHTML.indexOf('跨页') >= 0);
    check('当前停在第 1 页', pageLabel() === '1 / 2',
      pageLabel());
    scrollCalls.length = 0;
    const jumped = clickJumpChip(21);
    check('点第 2 页那一块的序号：先翻页再滚动',
      jumped && pageLabel() === '2 / 2',
      pageLabel());
    check('翻过去之后列表里真有那张卡片（否则点下去等于没反应）',
      cardIds().indexOf(21) >= 0, cardIds().join(','));

    console.log('\n[14] 删除跨页合并题：两页一起删');
    reset();
    addPage2();
    seedCrossMerge();
    render();
    requests.length = 0;
    sandbox.hideMistakeRecord(90);
    return flushDeep().then(function () {
      check('先把这个跨页组拆掉',
        crossPosts().length === 1
          && JSON.parse(crossPosts()[0].options.body).cross_merges.length === 0,
        'cross=' + crossPosts().length);
      const urls = hiddenPosts().map(function (p) { return p.url; });
      check('两页各发一次隐藏请求（另一半在别的页上，只删本页会留下半截题）',
        hiddenPosts().length === 2
          && urls.join('|').indexOf('/pages/1/hidden') >= 0
          && urls.join('|').indexOf('/pages/2/hidden') >= 0,
        JSON.stringify(urls));
      check('每页写的是「原有的 + 它自己那一半的矩形」',
        JSON.parse(hiddenPosts()[0].options.body).rects.length === 1
          && JSON.parse(hiddenPosts()[1].options.body).rects.length === 1,
        JSON.stringify(hiddenPosts().map(function (p) { return p.options.body; })));
      check('撤销栈里存了一步「删除跨页合并题」',
        store.undoStack.length === 1 && /删除跨页合并题/.test(store.undoStack[0].label),
        JSON.stringify(store.undoStack.map(function (e) { return e.label; })));

      console.log('\n============================================================');
      console.log(fail ? `有失败：${fail} 项（通过 ${pass} 项）` : `全部通过：${pass} 项`);
      process.exit(fail ? 1 : 0);
    });
  });
});
