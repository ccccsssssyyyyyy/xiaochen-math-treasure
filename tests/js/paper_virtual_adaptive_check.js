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

/** 按顶层 data-vcard 把卡片的 HTML 切成一段一段（每张卡一段）。 */
function splitVCards(html) {
  const re = /<div[^>]*\bdata-vcard\b[^>]*>/g;
  const starts = [];
  let m;
  while ((m = re.exec(html)) !== null) starts.push(m.index);
  const parts = [];
  for (let i = 0; i < starts.length; i++) {
    parts.push(html.slice(starts[i], i + 1 < starts.length ? starts[i + 1] : html.length));
  }
  return parts;
}

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
    offsetHeight: 0,
    style: {},
    dataset: {},
    handlers: {},
    children: [],
    parentNode: null,
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
    // 真实 DOM 的树操作。窗口同步靠这些复用卡片，假 DOM 必须真的维护 children，
    // 否则「复用了哪几张、重建了哪几张」在夹具里完全看不见。
    appendChild: function (node) {
      if (!node) return node;
      const existing = el.children.indexOf(node);
      if (existing >= 0) el.children.splice(existing, 1);
      el.children.push(node);
      node.parentNode = el;
      // 高度模型：卡在容器里的次序 → cardHeightQueue 对应项（未注入则沿用估计高 264）
      const pos = el.children.indexOf(node);
      node.offsetHeight = cardHeightQueue.length
        ? cardHeightQueue[Math.min(pos, cardHeightQueue.length - 1)]
        : 264;
      return node;
    },
    insertBefore: function (node, ref) {
      if (!node) return node;
      const existing = el.children.indexOf(node);
      if (existing >= 0) el.children.splice(existing, 1);
      const at = ref ? el.children.indexOf(ref) : -1;
      if (at >= 0) el.children.splice(at, 0, node); else el.children.push(node);
      node.parentNode = el;
      return node;
    },
    removeChild: function (node) {
      const i = el.children.indexOf(node);
      if (i >= 0) { el.children.splice(i, 1); node.parentNode = null; }
      return node;
    },
    remove: function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    },
    focus: function () {},
    click: function () {},
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: 1000, height: 2000, right: 1000, bottom: 2000 }; }
  };
  Object.defineProperty(el, 'firstElementChild', {
    get: function () { return el.children[0] || null; }
  });
  Object.defineProperty(el, 'nextElementSibling', {
    get: function () {
      const p = el.parentNode;
      if (!p) return null;
      const i = p.children.indexOf(el);
      return i >= 0 ? (p.children[i + 1] || null) : null;
    }
  });
  Object.defineProperty(el, 'innerHTML', {
    get: function () {
      // 卡片是 appendChild 进去的，读 innerHTML 就得按子元素序列化回来 ——
      // 否则「渲染出了什么」在夹具里看不见。
      if (el.children.length) return el.children.map(function (c) { return c._innerHTML; }).join('');
      return el._innerHTML;
    },
    set: function (html) {
      el._innerHTML = String(html);
      el.children = [];
      const parts = splitVCards(el._innerHTML);
      for (let i = 0; i < parts.length; i++) {
        const child = makeElement('fake-card-' + i);
        child._innerHTML = parts[i];
        child.parentNode = el;
        el.children.push(child);
      }
      // 真实浏览器里 container.innerHTML 重建会连旧的 spacer/viewport 一起丢掉，
      // 新的 viewport 是空的。假 DOM 必须照做，否则窗口同步会以为上一批卡片还在。
      if (/id="paperVirtualViewport"/.test(el._innerHTML)) {
        const vp = elementFor('paperVirtualViewport');
        vp.children = [];
        vp._innerHTML = '';
      }
      if (/id="paperVirtualSpacer"/.test(el._innerHTML)) {
        elementFor('paperVirtualSpacer').style = {};
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

// ---------------------------------------------------------------- [5] 滚动稳定性（2026-09-20）

section('[5] 滚动稳定性：窗口未变不重建、窗口变化复用卡片、无跨帧闭环');

// 60 题、视口 800px 的列表。先按「只取决于视口高度」算一遍窗口应有多大。
sandbox.PaperStore.bankQuestions = [];
for (let i = 1; i <= 60; i++) {
  sandbox.PaperStore.bankQuestions.push(fakeQuestion(100 + i, '第 ' + i + ' 题题干。'));
}
sandbox.PaperStore.cart = [];
sandbox.PaperStore.filters.tab = 'all';
M.resetMeasured();
cardHeightQueue = [];
container.scrollTop = 0;
sandbox.switchPaperStreamTab('all');

const vp5 = elementFor('paperVirtualViewport');
const win0 = M.lastWindow();
check('首屏窗口已建立', win0.start === 0 && win0.end > 0, JSON.stringify(win0));

// ① 窗口未变的那几帧：卡片元素引用必须逐个不变（证明不是「重建后内容恰好相同」）。
//    「窗口不变」的滚动上界由 offsets 现算，不写死常量 —— 估计高改了也不会误判。
const offs5 = M.offsets(sandbox.PaperStore.bankQuestions);
const idxAtBottom = M.indexAt(offs5, container.clientHeight);
const safeSpan = Math.max(1, Math.min(offs5[1] - 1, offs5[idxAtBottom + 1] - container.clientHeight - 1));
const before = vp5.children.slice();
M.resetStats();
for (let s = 0; s <= safeSpan; s += Math.max(1, Math.floor(safeSpan / 4))) {
  container.scrollTop = s;
  (container.handlers.scroll || []).forEach(function (fn) { fn(); });
}
const sameRefs = before.length === vp5.children.length
  && before.every(function (c, i) { return c === vp5.children[i]; });
check('窗口未变的那几帧：卡片元素引用逐个不变（没有整块重建）', sameRefs,
  'before=' + before.length + ' after=' + vp5.children.length);
check('窗口未变时一个 DOM 同步都没做', M.stats().domSyncs === 0 && M.stats().updates > 0,
  JSON.stringify(M.stats()));

// ② 滚过好几张卡（窗口必然变化）：应复用已在窗口里的卡，只增删差异。
const beforeWide = vp5.children.slice();
M.resetStats();
container.scrollTop = 4 * (offs5[1] || 280);
(container.handlers.scroll || []).forEach(function (fn) { fn(); });
const afterWide = vp5.children.slice();
const reused = beforeWide.filter(function (c) { return afterWide.indexOf(c) >= 0; }).length;
check('窗口变化时复用已在视口内的卡片（不是整块重建）', reused > 0,
  'reused=' + reused + ' before=' + beforeWide.length + ' after=' + afterWide.length);
check('一次窗口变化只做一次 DOM 同步', M.stats().domSyncs === 1, JSON.stringify(M.stats()));

// ③ 连续滚动：DOM 同步次数必须远小于帧数。旧实现是每帧一次（帧数 == 同步数）。
M.resetStats();
let frames = 0;
for (let s = 4 * (offs5[1] || 280); s <= 4 * (offs5[1] || 280) + 1500; s += 50) {
  container.scrollTop = s;
  (container.handlers.scroll || []).forEach(function (fn) { fn(); });
  frames++;
}
const st = M.stats();
// 旧实现每帧都同步（domSyncs === frames）；现在只有窗口真跨过卡边界才同步。
// 阈值取 frames/2 也是为了保留判别力：回退到每帧重建就会超。
check('连续滚动 ' + frames + ' 帧，DOM 同步次数远小于帧数',
  st.updates === frames && st.domSyncs <= frames / 2,
  JSON.stringify(st));

// ④ 跨帧闭环：程序修正 scrollTop 之后，浏览器派发的那次 scroll 必须被认出来。
M.resetMeasured();
cardHeightQueue = [400];            // 实测 400 ≠ 估计 264，必然触发一次锚点修正
container.scrollTop = 1500;
(container.handlers.scroll || []).forEach(function (fn) { fn(); });
const selfTop = M.pendingSelfScroll();
check('实测高度修正 scrollTop 时登记了 selfScroll 标记', typeof selfTop === 'number' && selfTop !== 1500,
  'pendingSelfScroll=' + selfTop);

container.scrollTop = selfTop;
M.resetStats();
(container.handlers.scroll || []).forEach(function (fn) { fn(); });
check('自己派发的 scroll 被识别，不再触发新一轮更新',
  M.stats().updates === 0 && M.stats().domSyncs === 0, JSON.stringify(M.stats()));

// 回归护栏：滚动路径里不允许再出现整块重建。
// 只查代码行 —— 改动说明里会引用旧写法当反例，那不是漏网。
const codeLines = src.split('\n').filter(function (l) { return !/^\s*(\/\/|\*)/.test(l); });
check('源码里不再有 viewport.innerHTML = （回归护栏）',
  !codeLines.some(function (l) { return /viewport\.innerHTML\s*=/.test(l); }));

// ---------------------------------------------------------------- 汇总

console.log('\n通过 ' + pass + ' 项，失败 ' + fail + ' 项');
process.exit(fail ? 1 : 0);
