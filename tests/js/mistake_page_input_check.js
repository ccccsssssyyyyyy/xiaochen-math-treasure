/**
 * 错题工作台「页码跳转 + 记住停留页」实跑校验 —— 真实执行 mistake.js，不靠正则猜源码。
 *
 * 这两件事都属于「看着能用、其实没生效」的高发区：
 * 1) 页号输入框不收敛越界值，会跳到一个不存在的页 —— 页图白屏，也没报错；
 * 2) 「记住停留页」有好几个页面切换入口（箭头翻页、输入跳转、跨页跳转），
 *    漏掉任何一个都只在「退出再进」时才暴露，平时完全看不出来；
 * 3) 反过来还有个更隐蔽的：后台任何一次重绘都可能把用户正打字的输入框覆盖掉。
 *
 * 所以这里用假 DOM 把 mistake.js 整个装进 vm 沙箱，真跑「输入 → 提交 → 翻页 →
 * 退出 → 重进」，断言 store.pageIndex、输入框的值，以及 localStorage 里到底写了什么。
 *
 * 用法: node tests/js/mistake_page_input_check.js [mistake.js 路径]
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
    // 真实 DOM 的 input 一定有 value。缺了它，读 input.value 会拿到 undefined，
    // 「读自己刚写的值」这类逻辑在测试里就永远走异常分支。
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

const documentHandlers = {};
const requests = [];
const timers = [];
let timerSeq = 1;

function runTimers() {
  const queued = timers.splice(0, timers.length);
  queued.forEach(function (timer) { timer.fn(); });
}

function jsonResponse(ok, status, data) {
  return Promise.resolve({ ok: ok, status: status, json: function () { return Promise.resolve(data); } });
}

/** 真存的 localStorage —— 本组用例的核心就是「写了什么、下次读回什么」。 */
const storage = {};

/** 假后端：批次页数可调，用来验「重切后页数变少」这类边界。 */
let pageCount = 3;
function detailPayload(batchId) {
  const pages = [];
  for (let i = 1; i <= pageCount; i++) {
    pages.push({
      page_no: i,
      url: '/page_' + i + '.png',
      width: 1000,
      height: 2000,
      layout: { mode: 'single', boundary: 1.0, source: 'text_layer', confidence: 1.0 },
      column_boundary: 1.0,
      snap_points: [], gap_candidates: [],
      column_snap_points: {}, column_gap_candidates: {},
      manual_boxes: []
    });
  }
  return {
    batch: { id: batchId, subject: 'physics', title: '测试卷', page_count: pageCount },
    records: [],
    pages: pages,
    question_types: [],
    mistake_reasons: []
  };
}

const sandbox = {
  console: console,
  setTimeout: function (fn, delay) { const id = timerSeq++; timers.push({ id: id, fn: fn, delay: delay || 0 }); return id; },
  clearTimeout: function (id) { const i = timers.findIndex(function (t) { return t.id === id; }); if (i >= 0) timers.splice(i, 1); },
  setInterval: function () { return 0; },
  clearInterval: function () {},
  confirm: function () { return true; },
  fetch: function (url, options) {
    requests.push({ url: url, options: options || {} });
    const detail = /^\/api\/mistakes\/batches\/(\d+)$/.exec(url);
    if (detail) return jsonResponse(true, 200, detailPayload(Number(detail[1])));
    if (/\/cut$/.test(url)) return jsonResponse(true, 200, { task_id: 'local' });
    if (/^\/api\/tasks\//.test(url)) return jsonResponse(true, 200, { status: 'completed', block_count: 0, steps: [] });
    if (/^\/api\/mistakes\/batches$/.test(url)) return jsonResponse(true, 200, { batches: [] });
    return jsonResponse(true, 200, {});
  },
  localStorage: {
    getItem: function (key) {
      return Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : null;
    },
    setItem: function (key, value) { storage[key] = String(value); },
    removeItem: function (key) { delete storage[key]; }
  },
  document: {
    getElementById: elementFor,
    addEventListener: function (type, fn) { (documentHandlers[type] = documentHandlers[type] || []).push(fn); },
    removeEventListener: function () {},
    querySelector: function () { return null; },
    // 重绘时要靠它判断「用户正在输入，别覆盖」；浏览器默认 null
    activeElement: null,
    documentElement: { classList: { add: function () {}, remove: function () {} } }
  }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
require('./sandbox_base').loadBaseModules(sandbox);
vm.runInContext(src, sandbox, { filename: 'mistake.js' });

const store = sandbox.MistakeStore;
const pageInput = elementFor('mistakePageInput');
const pageTotal = elementFor('mistakePageTotal');
const cardList = elementFor('mistakeCardList');

let blurCalls = 0;
pageInput.blur = function () { blurCalls += 1; };

function fireDocument(type, payload) {
  (documentHandlers[type] || []).slice().forEach(function (fn) { fn(payload || {}); });
}
fireDocument('DOMContentLoaded');

/** 跑掉 fetch → then → render 的微任务链。 */
function flush() {
  return Promise.resolve()
    .then(function () { return Promise.resolve(); })
    .then(function () { runTimers(); return Promise.resolve(); })
    .then(function () { return Promise.resolve(); })
    .then(function () { runTimers(); return Promise.resolve(); });
}

/** 把页面摆成「详情页可见」的样子（部分重绘有可见性门禁）。 */
function render() {
  elementFor('mistakeWorkspaceSection').classList.remove('hidden');
  elementFor('mistakeDetailView').classList.remove('hidden');
  sandbox.setMistakeLayout('auto');
  sandbox.setMistakeCardFilter('all');
}

const PAGE_KEY = 'mathbank_mistake_page_7';

/** 在输入框里打字并提交（走的是 change 事件的入口）。 */
function typePage(value) {
  pageInput.value = value;
  sandbox.mistakePageCommit();
}

/** 在输入框里按回车（走的是 keydown 的入口）。 */
function pressEnter() {
  sandbox.mistakePageInputKey({
    key: 'Enter',
    target: pageInput,
    preventDefault: function () {}
  });
}

function pressEscape() {
  sandbox.mistakePageInputKey({
    key: 'Escape',
    target: pageInput,
    preventDefault: function () {}
  });
}

/** 进入某个批次并把异步链跑完。 */
function openBatch(batchId) {
  sandbox.openMistakeBatch(batchId);
  return flush();
}

// ---------------------------------------------------------------- 用例

console.log('\n[1] 输入页号直接跳过去（change 入口）');
pageCount = 3;
store.pages = detailPayload(7).pages;
store.batch = { id: 7 };
store.pageIndex = 0;
render();
check('起始停在第 1 页', pageInput.value === '1' && pageTotal.textContent === '/ 3',
  pageInput.value + ' ' + pageTotal.textContent);
typePage('3');
check('输入 3 → 跳到第 3 页', store.pageIndex === 2, String(store.pageIndex));
check('输入框自己归位到 3（不是留着原样的 ' + '1）', pageInput.value === '3', pageInput.value);
check('页号变化已经落盘', storage[PAGE_KEY] === '2', JSON.stringify(storage));
typePage('1');
check('再输 1 → 回到第 1 页', store.pageIndex === 0 && pageInput.value === '1', String(store.pageIndex));

console.log('\n[2] 回车与 change 走同一条路（Enter 提交并失焦）');
blurCalls = 0;
pageInput.value = '2';
pressEnter();
check('回车 → 跳到第 2 页', store.pageIndex === 1, String(store.pageIndex));
check('回车后主动失焦（否则光标还留在框里，看着像没提交）', blurCalls === 1, String(blurCalls));
check('输入框归位到 2', pageInput.value === '2', pageInput.value);

console.log('\n[3] 越界的值静默收敛，不跳到不存在的页');
typePage('99');
check('输入 99 → 收到最后一页', store.pageIndex === 2, String(store.pageIndex));
check('输入框里显示收拢后的 3（不能还留着 99）', pageInput.value === '3', pageInput.value);
store.pageIndex = 1;
typePage('0');
check('输入 0 → 落到第 1 页', store.pageIndex === 0, String(store.pageIndex));
store.pageIndex = 1;
typePage('-5');
check('输入负数 → 落到第 1 页', store.pageIndex === 0 && pageInput.value === '1', pageInput.value);
store.pageIndex = 1;
typePage('2.7');
check('输入小数 → 向下取整到第 2 页', store.pageIndex === 1, String(store.pageIndex));

console.log('\n[4] 非数字与空格子：还原成当前页号，不跳也不白屏');
store.pageIndex = 1;
typePage('abc');
check('输入 abc → 页不动', store.pageIndex === 1, String(store.pageIndex));
check('输入框还原成当前页号 2', pageInput.value === '2', pageInput.value);
typePage('');
check('清空后提交 → 页不动、值还原', store.pageIndex === 1 && pageInput.value === '2', pageInput.value);
typePage('   ');
check('只打空格 → 同样还原', store.pageIndex === 1 && pageInput.value === '2', pageInput.value);

console.log('\n[5] Esc 撤销这次输入，回到当前页号');
pageInput.value = '3';
pressEscape();
check('Esc 后输入框回到当前页号 2', pageInput.value === '2', pageInput.value);
check('Esc 不改变所在页', store.pageIndex === 1, String(store.pageIndex));

console.log('\n[6] 箭头翻页同样落盘（不只输入框那条路）');
store.pageIndex = 0;
sandbox.mistakePageStep(1);
check('翻到第 2 页', store.pageIndex === 1, String(store.pageIndex));
check('翻页也写进了 localStorage', storage[PAGE_KEY] === '1', JSON.stringify(storage));
check('输入框跟着变成 2', pageInput.value === '2', pageInput.value);

console.log('\n[7] 到头的箭头：不动，也不把右栏列表滚回顶部');
store.pageIndex = 0;
cardList.scrollTop = 400;
sandbox.mistakePageStep(-1);
check('已在第 1 页再点「上一页」→ 页不动', store.pageIndex === 0, String(store.pageIndex));
check('右栏滚动位置没被顺手重置', cardList.scrollTop === 400, String(cardList.scrollTop));
store.pageIndex = 2;
cardList.scrollTop = 400;
sandbox.mistakePageStep(1);
check('已在最后一页再点「下一页」→ 页不动', store.pageIndex === 2, String(store.pageIndex));
check('右栏滚动位置同样保住', cardList.scrollTop === 400, String(cardList.scrollTop));

console.log('\n[8] 退出再进同一批次 → 回到离开时那一页');
store.pageIndex = 2;
sandbox.mistakePageStep(0);          // 触发一次落盘（相当于停在第 3 页）
storage[PAGE_KEY] = '2';             // 明确写下「离开时是第 3 页」
sandbox.backToMistakeList();
store.pageIndex = 0;                 // 模拟「重新进来时状态是干净的」
return openBatch(7).then(function () {
  check('重进批次后回到第 3 页，而不是第 1 页', store.pageIndex === 2, String(store.pageIndex));
  check('输入框显示 3', pageInput.value === '3', pageInput.value);

  console.log('\n[9] 不同批次各记各的，不会串');
  storage[PAGE_KEY] = '2';
  store.pages = detailPayload(8).pages;
  store.pageIndex = 0;
  return openBatch(8).then(function () {
    check('批次 8 没有记录 → 老老实实从第 1 页开始', store.pageIndex === 0, String(store.pageIndex));
    check('没顺手把批次 7 的记录写脏', storage[PAGE_KEY] === '2', JSON.stringify(storage));

    console.log('\n[10] 重切后页数变少：收拢到最后一页，并把修正值写回去');
    storage[PAGE_KEY] = '2';         // 记的是「第 3 页」，但页面只剩 2 页了
    pageCount = 2;
    store.pageIndex = 0;
    return openBatch(7).then(function () {
      check('收拢到最后一页（第 2 页）', store.pageIndex === 1, String(store.pageIndex));
      check('修正后的页号已落盘，下次进来不会再算一遍', storage[PAGE_KEY] === '1', JSON.stringify(storage));

      console.log('\n[11] 正在输入时，后台重绘不许覆盖输入框');
      pageCount = 3;
      store.pages = detailPayload(7).pages;
      store.pageIndex = 0;
      render();
      sandbox.document.activeElement = pageInput;
      pageInput.value = '3';             // 用户正在打「3」，还没回车
      sandbox.mistakeZoom(0.25);         // 触发一次 renderPageView
      check('输入框里还是用户打的 3', pageInput.value === '3', pageInput.value);
      sandbox.document.activeElement = null;
      sandbox.mistakeZoom(-0.25);
      check('不在输入框里时，重绘正常把它写回当前页号', pageInput.value === '1', pageInput.value);

      console.log('\n[12] 没有页图（还没切题）时提交页号不炸');
      store.pages = [];
      store.batch = { id: 7 };
      typePage('2');
      check('输入框被清空而不是留着 2', pageInput.value === '', JSON.stringify(pageInput.value));
      check('页码保持在第 1 页', store.pageIndex === 0, String(store.pageIndex));

      console.log('\n[13] localStorage 不可用（无痕模式）时，翻页本身不能受影响');
      store.pages = detailPayload(7).pages;
      store.pageIndex = 0;
      const realGet = sandbox.localStorage.getItem;
      const realSet = sandbox.localStorage.setItem;
      sandbox.localStorage.getItem = function () { throw new Error('隐私模式'); };
      sandbox.localStorage.setItem = function () { throw new Error('隐私模式'); };
      sandbox.mistakePageStep(1);
      check('读不到记录也不影响翻页', store.pageIndex === 1, String(store.pageIndex));
      return openBatch(7).then(function () {
        check('读 localStorage 抛异常时也不炸，回落到第 1 页', store.pageIndex === 0, String(store.pageIndex));
        sandbox.localStorage.getItem = realGet;
        sandbox.localStorage.setItem = realSet;

        console.log('\n[14] 删批次时顺手清掉它的停留页记录');
        storage[PAGE_KEY] = '2';
        sandbox.deleteMistakeBatch(7);
        return flush().then(function () {
          check('批次 7 的停留页记录被清掉', !Object.prototype.hasOwnProperty.call(storage, PAGE_KEY),
            JSON.stringify(storage));

          console.log('\n============================================================');
          if (fail) {
            console.log('失败 ' + fail + ' 项 / 共 ' + (pass + fail) + ' 项');
            process.exit(1);
          }
          console.log('全部通过：' + pass + ' 项');
        });
      });
    });
  });
});
