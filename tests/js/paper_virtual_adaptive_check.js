/**
 * 组卷选题流「高度自适应虚拟列表」校验 —— 真实执行 paper.js，不靠正则猜源码。
 *
 * 2026-09-18 用户截图实测：上一张卡(#1406)的内容超高溢出固定 264px 卡片（容器不裁剪），
 * 插图按浏览器绘制顺序盖在下一张卡(#1405)的「加入试卷」按钮上 —— 按钮看得见、点不到。
 * 修复改成：卡片自然高度 + 渲染后实测回填 + 前缀和定位 + overflow-hidden 兜底。
 *
 * 这里用假 DOM 把 paper.js 整个加载进 vm 沙箱，断言：
 *   [1] 纯数学：前缀和 offsets、二分 indexAt、槽高估计/实测优先级；
 *   [2] 卡片结构：不再有 style="height:264px"，带 overflow-hidden 兜底与 16px 间距；
 *   [3] 实测回填：渲染后把假 DOM 量出的卡高写回缓存，spacer/transform 用实测值重算；
 *   [4] 回归护栏：PaperVirtualMath 暴露面完整（契约测试的依赖）。
 *
 * 用法: node tests/js/paper_virtual_adaptive_check.js [paper.js 路径]
 * 退出码: 0 全通过 / 1 有失败
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const paperPath = process.argv[2]
  || path.join(__dirname, '..', '..', 'static', 'js', 'paper.js');
const src = fs.readFileSync(paperPath, 'utf8');

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}
function section(title) { console.log('\n' + title); }

// ---------------------------------------------------------------- 假 DOM

/** 测试注入：viewport 每次 innerHTML 重设后，按这份队列给每张卡发 offsetHeight */
let cardHeightQueue = [];

function makeElement(id) {
  const classes = new Set();
  const el = {
    id: id,
    _innerHTML: '',
    textContent: '',
    value: '',
    scrollTop: 0,
    scrollHeight: 4000,
    clientHeight: 800,
    style: {},
    dataset: {},
    handlers: {},
    children: [],
    classList: {
      add: function () { for (const c of arguments) classes.add(c); },
      remove: function () { for (const c of arguments) classes.delete(c); },
      toggle: function () {},
      contains: function (c) { return classes.has(c); }
    },
    setAttribute: function (n, v) { el.dataset['attr_' + n] = String(v); },
    getAttribute: function (n) { return el.dataset['attr_' + n]; },
    removeAttribute: function (n) { delete el.dataset['attr_' + n]; },
    addEventListener: function (t, f) { (el.handlers[t] = el.handlers[t] || []).push(f); },
    removeEventListener: function () {},
    appendChild: function () {},
    remove: function () {},
    focus: function () {},
    click: function () {},
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: 1000, height: 2000, right: 1000, bottom: 2000 }; }
  };
  Object.defineProperty(el, 'innerHTML', {
    get: function () { return el._innerHTML; },
    set: function (html) {
      el._innerHTML = String(html);
      // 只有虚拟流视口按卡片标记生成可量测的子元素；其余元素给空 children
      const count = (el._innerHTML.match(/data-vcard/g) || []).length;
      el.children = [];
      for (let i = 0; i < count; i++) {
        el.children.push({
          offsetHeight: cardHeightQueue.length ? cardHeightQueue[Math.min(i, cardHeightQueue.length - 1)] : 264,
          id: 'fake-card-' + i
        });
      }
    }
  });
  return el;
}

const elements = {};
function elementFor(id) {
  if (!elements[id]) elements[id] = makeElement(id);
  return elements[id];
}

const documentHandlers = {};

const sandbox = {
  console: console,
  setTimeout: function (fn) { if (typeof fn === 'function') fn(); return 1; },
  clearTimeout: function () {},
  setInterval: function () { return 0; },
  clearInterval: function () {},
  requestAnimationFrame: function (fn) { if (typeof fn === 'function') fn(); return 1; },
  ResizeObserver: function (cb) { this.cb = cb; this.observe = function () {}; this.disconnect = function () {}; },
  fetch: function () {
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve([]); } });
  },
  localStorage: { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} },
  document: {
    getElementById: elementFor,
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    createElement: function () { return makeElement('created'); },
    addEventListener: function (t, f) { (documentHandlers[t] = documentHandlers[t] || []).push(f); },
    removeEventListener: function () {},
    body: makeElement('body'),
    documentElement: { classList: { add: function () {}, remove: function () {} } },
    activeElement: null
  }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.URLSearchParams = URLSearchParams;
sandbox.MathBankSafe = {
  escapeText: function (v) { return v == null ? '' : String(v); },
  escapeAttribute: function (v) { return v == null ? '' : String(v); },
  sanitizePlainText: function (v) { return v == null ? '' : String(v); },
  sanitizeRichHtml: function (v) { return v == null ? '' : String(v); },
  safeClassList: function (v, fallback) { return fallback || ''; },
  safeImageUrl: function () { return ''; }
};
sandbox.MathRender = { render: function () {}, renderMathIn: function () {} };
sandbox.showToast = function () {};

vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: 'paper.js' });

const M = sandbox.PaperVirtualMath;
if (!M) {
  console.log('  FAIL  window.PaperVirtualMath 未暴露');
  process.exit(1);
}

function fakeQuestion(id, content) {
  return {
    id: id, seq_num: id, question_type: 'single_choice', difficulty: 'normal',
    subject: 'physics', content: content || ('第 ' + id + ' 题题干。'),
    knowledge_list: '运动学'
  };
}

// ---------------------------------------------------------------- [1] 纯数学

section('[1] 前缀和 / 二分 / 槽高');

M.resetMeasured();
const list = [fakeQuestion(1), fakeQuestion(2), fakeQuestion(3)];
M.setMeasuredHeight(1, 100);
M.setMeasuredHeight(2, 200);
// q3 未实测 → 用估计 264
const offs = M.offsets(list);
const gap = M.GAP, est = M.ESTIMATE;
check('槽高 = 实测卡高 + 间距（q1: 100+16）', M.slotHeightOf(list[0]) === 100 + gap);
check('未实测的卡回落到估计高（q3: 264+16）', M.slotHeightOf(list[2]) === est + gap);
check('offsets 前缀和正确', JSON.stringify(offs) === JSON.stringify([0, 116, 332, 332 + est + gap]),
  'offs=' + JSON.stringify(offs));
check('indexAt(0) = 0', M.indexAt(offs, 0) === 0);
check('indexAt(槽1末尾前) = 0', M.indexAt(offs, 100 + gap - 1) === 0);
check('indexAt(恰在槽2顶) = 1', M.indexAt(offs, 100 + gap) === 1);
check('indexAt(槽2末尾前) = 1', M.indexAt(offs, 332 - 1) === 1);
check('indexAt(恰在槽3顶) = 2', M.indexAt(offs, 332) === 2);
check('indexAt(超出总高) 停在最后一张', M.indexAt(offs, 99999) === 2);
check('空列表 offsets=[0] 且 indexAt=0',
  JSON.stringify(M.offsets([])) === '[0]' && M.indexAt([0], 500) === 0);
check('单卡列表 indexAt 恒 0', M.indexAt([0, 500], 499) === 0 && M.indexAt([0, 500], 500) === 0);
check('resetMeasured 清空缓存', (M.resetMeasured(), M.measuredHeight(1) === undefined));

// ---------------------------------------------------------------- [2] 卡片结构

section('[2] 卡片结构：去固定高 + overflow 兜底 + 真实间距');

sandbox.PaperStore.bankQuestions = [fakeQuestion(11, '题干甲。'), fakeQuestion(12, '题干乙。')];
sandbox.PaperStore.cart = [];
sandbox.PaperStore.filters.tab = 'all';
M.resetMeasured();
cardHeightQueue = [];   // 未注入 → 假卡全按 264 量
sandbox.switchPaperStreamTab('all');

const streamHtml = elementFor('paperVirtualViewport').innerHTML;
check('每张卡带 data-vcard 标记', (streamHtml.match(/data-vcard/g) || []).length === 2);
check('卡片不再写死 style="height:264px"', !/style="height:\s*264/.test(streamHtml));
check('卡片带 overflow-hidden 兜底（溢出内容不再盖到下一张卡）', streamHtml.indexOf('overflow-hidden') >= 0);
check('卡片间距落在 margin-bottom:16px 上', streamHtml.indexOf('margin-bottom:16px') >= 0);

// ---------------------------------------------------------------- [3] 实测回填

section('[3] 渲染后实测：缓存回填 + spacer/transform 用实测值');

const vp = elementFor('paperVirtualViewport');
const spacer = elementFor('paperVirtualSpacer');
const container = elementFor('paperQuestionStream');
M.resetMeasured();
cardHeightQueue = [420, 200];          // 卡1 实际 420px（超高），卡2 200px
sandbox.switchPaperStreamTab('all');

check('实测高度写回缓存（q11=420）', M.measuredHeight(11) === 420,
  'measured=' + M.measuredHeight(11));
check('实测高度写回缓存（q12=200）', M.measuredHeight(12) === 200,
  'measured=' + M.measuredHeight(12));
check('spacer 总高用实测值（420+16 + 200+16 = 652）', spacer.style.height === '652px',
  'spacer=' + spacer.style.height);
check('viewport transform 锚到首卡实测顶部', vp.style.transform === 'translateY(0px)',
  'transform=' + vp.style.transform);

// 滚动到 500px：应落在卡2（卡1槽 436px < 500），窗口仍要罩住它
container.scrollTop = 500;
(container.handlers.scroll || []).forEach(function (fn) { fn(); });
check('滚动后 transform 用实测前缀和定位', vp.style.transform === 'translateY(0px)',
  'transform=' + vp.style.transform);
check('滚动后窗口仍渲染到卡2', vp.innerHTML.indexOf('题干乙。') >= 0);

// 换一批题：高度缓存按 q.id 复用，不会被列表重建清掉
sandbox.PaperStore.bankQuestions = [fakeQuestion(11, '题干甲。'), fakeQuestion(13, '题干丙。')];
cardHeightQueue = [420, 264];   // q13 的假 DOM 实际高度就是 264
sandbox.switchPaperStreamTab('all');
check('列表重建后 q11 的实测高度仍复用（420）', M.measuredHeight(11) === 420);
check('新题 q13 按自身实测量得（264）参与 spacer（420+16 + 264+16 = 716）',
  M.measuredHeight(13) === 264 && spacer.style.height === '716px',
  'measured13=' + M.measuredHeight(13) + ' spacer=' + spacer.style.height);

// ---------------------------------------------------------------- [4] 暴露面

section('[4] 契约测试依赖的暴露面');

check('offsets / indexAt / slotHeightOf 是函数',
  [M.offsets, M.indexAt, M.slotHeightOf].every(function (f) { return typeof f === 'function'; }));
check('ESTIMATE=264 且 GAP=16', M.ESTIMATE === 264 && M.GAP === 16);

// ---------------------------------------------------------------- 汇总

console.log('\n通过 ' + pass + ' 项，失败 ' + fail + ' 项');
process.exit(fail ? 1 : 0);
