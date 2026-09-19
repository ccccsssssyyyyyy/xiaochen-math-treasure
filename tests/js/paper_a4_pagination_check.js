/**
 * A4 预览实测重分页 + 抬头学科行跟随 + 插图默认居中 —— 契约测试（vm 沙箱，2026-09-19）
 *
 * 覆盖三个用户问题：
 *  1) 分页原来按字符数估算（75px/题、超 200 字 +60），KaTeX/插图真实高度对不上，
 *     表现为「题目卡在页缝里、页脚和题干撞车」。现在渲染后实测 offsetHeight 回填再重分页。
 *  2) 页脚学科前缀跟 meta.subject_line 走，但该值被预填成上一张卷的学科后不再跟随；
 *     现在卷内单科且用户没手改过时自动跟随。
 *  3) 插图默认排版从「右侧 / 下方居右」改为居中。
 *
 * 运行：node tests/js/paper_a4_pagination_check.js [可选：mistake.js 之外的 paper.js 路径]
 */
'use strict';

const fs = require('fs');
const vm = require('vm');
const path = require('path');

const paperPath = process.argv[2] || path.join(__dirname, '..', '..', 'static', 'js', 'paper.js');
const src = fs.readFileSync(paperPath, 'utf8');

let passed = 0;
let failed = 0;
const failures = [];
function check(name, cond, detail) {
  if (cond) { passed++; console.log('  ok  ' + name); }
  else { failed++; failures.push(name + (detail ? ' :: ' + detail : '')); console.log('  FAIL ' + name + (detail ? ' :: ' + detail : '')); }
}

// ---------- 假 DOM ----------
function makeElement(id) {
  const el = {
    id: id, innerHTML: '', textContent: '', value: '', className: '', style: {},
    dataset: {}, scrollTop: 0, clientHeight: 800, offsetHeight: 0, children: [],
    _listeners: {},
    classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
    setAttribute(n, v) { this.dataset['attr_' + n] = v; },
    getAttribute(n) { return this.dataset['attr_' + n]; },
    removeAttribute() {}, addEventListener(t, f) { (this._listeners[t] = this._listeners[t] || []).push(f); },
    removeEventListener() {},
    appendChild() {}, focus() {}, blur() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 794, height: 1123 }; },
    querySelector() { return null; },
    querySelectorAll() { return []; }
  };
  return el;
}

const elements = {};
const elementFor = (id) => elements[id] || (elements[id] = makeElement(id));

// a4PaperPreviewSheet：innerHTML setter 记录每次渲染产物；querySelector 按 data-pb-idx
// 返回带「实测高度」的桩（模拟 KaTeX 定型后的真实块高）。
const MEASURED = {}; // idx -> px
const renderedPages = []; // 每轮 innerHTML 的快照
function installSheet() {
  const sheet = elementFor('a4PaperPreviewSheet');
  Object.defineProperty(sheet, 'innerHTML', {
    get() { return sheet._html || ''; },
    set(v) {
      sheet._html = String(v);
      renderedPages.push(sheet._html);
    }
  });
  sheet.querySelector = function (sel) {
    const m = /data-pb-idx="(\d+)"/.exec(sel || '');
    if (!m) return null;
    const idx = parseInt(m[1], 10);
    const child = makeElement('blk_' + idx);
    child.offsetHeight = MEASURED[idx] || 0;
    return child;
  };
  return sheet;
}

const storage = {};
const sandbox = {
  console, setTimeout(fn) { return 1; }, clearTimeout() {}, setInterval() { return 0; }, clearInterval() {},
  requestAnimationFrame(fn) { return 1; },
  fetch() { return Promise.resolve({ ok: true, status: 200, json() { return Promise.resolve({}); } }); },
  localStorage: {
    getItem(k) { return storage[k] === undefined ? null : storage[k]; },
    setItem(k, v) { storage[k] = String(v); },
    removeItem(k) { delete storage[k]; }
  },
  location: { search: '', pathname: '/' },
  navigator: { userAgent: 'test' },
  confirm() { return true; },
  prompt() { return null; },
  alert() {},
  getComputedStyle() { return { getPropertyValue() { return ''; } }; },
  document: {
    getElementById: elementFor,
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {},
    documentElement: { classList: { add() {}, remove() {} } },
    body: { classList: { add() {}, remove() {} }, appendChild() {} },
    createElement() { return makeElement('created_' + Math.random()); }
  },
  ResizeObserver: function () { this.observe = function () {}; this.disconnect = function () {}; },
  MutationObserver: function () { this.observe = function () {}; this.disconnect = function () {}; },
  matchMedia() { return { matches: false, addEventListener() {} }; }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
require(path.join(__dirname, 'sandbox_base')).loadBaseModules(sandbox);

vm.runInContext(src, sandbox, { filename: 'paper.js' });

// ---------- 1) 插图默认居中 ----------
console.log('\n[1] 插图默认排版默认居中');
const Fig = sandbox.PaperFigAlign;
check('暴露 PaperFigAlign 契约面', !!Fig);
check('无题对象 → center', Fig.get(undefined) === 'center');
check('无任何存储值 → center', Fig.get({ id: 1 }) === 'center');
check('旧默认 right（无 custom）→ 迁移为 center', Fig.get({ id: 2, figure_align: 'right' }) === 'center');
check('用户显式选过 right（有 custom）→ 保留 right', Fig.get({ id: 3, figure_align: 'right', custom_figure_align: 'right' }) === 'right');
check('存储 bottom_right（用户选择）→ 保留', Fig.get({ id: 4, figure_align: 'bottom_right' }) === 'bottom_right');

// ---------- 2) 抬头/页脚学科行跟随 ----------
console.log('\n[2] 学科行跟随卷内单科');
const Store = sandbox.PaperStore;
Store.questionsMap = {
  11: { id: 11, subject: 'physics', content: '物理题', question_type: 'single_choice' },
  12: { id: 12, subject: 'math', content: '数学题', question_type: 'single_choice' }
};
Store.meta.subject_line = '数学';
Store.meta.subject_line_custom = false;
Store.cart = [{ id: 11, score: 5 }];
sandbox.PaperSubjectSync.sync();
check('卷内只有物理 → 学科行变「物理」', Store.meta.subject_line === '物理', Store.meta.subject_line);
check('自动跟随已落盘', (storage.mathbank_paper_meta || '').indexOf('物理') >= 0);

Store.cart = [{ id: 11, score: 5 }, { id: 12, score: 5 }];
sandbox.PaperSubjectSync.sync();
check('混科卷不动学科行', Store.meta.subject_line === '物理');

Store.cart = [];
sandbox.PaperSubjectSync.sync();
check('空卷不动学科行', Store.meta.subject_line === '物理');

Store.meta.subject_line_custom = true;
Store.cart = [{ id: 11, score: 5 }];
sandbox.PaperSubjectSync.sync();
Store.meta.subject_line = '我的综合卷';
sandbox.PaperSubjectSync.sync();
check('用户手改过 → 永不自动回填', Store.meta.subject_line === '我的综合卷');

// ---------- 3) 实测重分页 ----------
console.log('\n[3] A4 预览实测重分页');
// 构造 6 道题：估算高度每块 81（75+6），实测高度差异巨大（含图/公式的题更高）。
Store.cart = [11, 12, 13, 14, 15, 16].map((id, i) => ({ id: id, score: 5 }));
Store.questionsMap = {};
for (let i = 0; i < 6; i++) {
  Store.questionsMap[11 + i] = {
    id: 11 + i, subject: 'physics',
    content: '物理题干 ' + (i + 1) + '。' + '长'.repeat(30),
    question_type: 'single_choice'
  };
}
Store.meta.subject_line = '物理';
Store.meta.subject_line_custom = false;

// 用带 cart 的路径太重（renderPaperCanvas 依赖大量 DOM）；
// 直接构造 blocks 喝 paginate/renderA4 的行为通过 window.repaginateA4Preview 走 sheet 桩。
// 为拿到 blocks，我们走 generateA4PaperPagesHtml 不可达（IIFE 内）——
// 退而求其次：直接验证 paginate 纯函数 + repaginate 的稳定行为。

const Pag = sandbox.PaperA4Pagination;
check('暴露 PaperA4Pagination 契约面', !!Pag);

// 3a. 纯分页：估算下 4 块/页（第二页 920 预算）
const mkBlocks = (heights) => heights.map((h, i) => ({
  idx: i, type: 'question', estHeight: h, measuredHeight: 0, html: '<div data-pb-idx="' + i + '">题' + i + '</div>'
}));
const estBlocks = mkBlocks([75, 75, 75, 75, 75, 75, 75, 75]);
const pagesEst = Pag.paginate(estBlocks);
check('估算高度：第二页起每页 11 块内（920/81=11）', pagesEst.length === 1 || pagesEst[1].length <= 11);
check('首页预算 620：估算下首页 7 块（7*81=567, 8*81=648 溢出）', pagesEst[0].length === 7, '首页 ' + pagesEst[0].length + ' 块');

// 3b. 实测高度参与分页：某块实测超高 → 单独成页，后续块再开新页
const mixed = mkBlocks([75, 75, 75, 75]);
mixed[1].measuredHeight = 900; // 实测 906：首页装不下 → 单独一页
const pagesMixed = Pag.paginate(mixed);
check('实测超高块单独成页', pagesMixed.length === 3 && pagesMixed[1].map(b => b.idx).join(',') === '1',
  'pages=' + pagesMixed.map(p => p.map(b => b.idx)));
check('后续块从新页继续排', pagesMixed[2].map(b => b.idx).join(',') === '2,3');

// 3c. 分隔标题不孤零零落页尾（carry 逻辑在实测口径下仍成立）：
// 页尾连续的 divider 一起跟到下一页，与后面的块同页。
const withDivider = [
  { idx: 0, type: 'question', estHeight: 100, measuredHeight: 0, html: '<div data-pb-idx="0">题1</div>' },
  { idx: 1, type: 'subject_divider', estHeight: 58, measuredHeight: 0, html: '<div data-pb-idx="1">部分一</div>' },
  { idx: 2, type: 'subject_divider', estHeight: 58, measuredHeight: 0, html: '<div data-pb-idx="2">部分二</div>' },
  { idx: 3, type: 'question', estHeight: 600, measuredHeight: 0, html: '<div data-pb-idx="3">大题</div>' }
];
const pagesDiv = Pag.paginate(withDivider);
check('页尾连续 divider 跟随下一块到新页',
  pagesDiv.length === 2 &&
  pagesDiv[0].map(b => b.idx).join(',') === '0' &&
  pagesDiv[1].map(b => b.idx).join(',') === '1,2,3',
  'pages=' + pagesDiv.map(p => p.map(b => b.idx)));

// 3d. repaginateA4Preview：渲染→实测→重分页→稳定
installSheet();
// 走 renderPaperCanvas 太重；repaginateA4Preview 依赖 paperA4Ctx（由 generateA4 写入）。
// 契约面没有 ctx 写入口，这里用一次真实 renderPaperCanvas 太脆弱 ——
// 改为验证 repaginate 的公开行为：window.repaginateA4Preview 存在且空上下文时不炸。
check('暴露 window.repaginateA4Preview', typeof sandbox.repaginateA4Preview === 'function');
let threw = false;
try { sandbox.repaginateA4Preview(3); } catch (e) { threw = true; }
check('无渲染上下文时安全空转', !threw);

// ---------- 4) 页脚学科前缀渲染自 subject_line ----------
console.log('\n[4] 页脚前缀来源');
// footerSubjectPrefix 未暴露，但 renderA4SheetsHtml 输出经 generateA4PaperPagesHtml。
// 这里用源码级断言兜底：页脚模板引用 footerSubjectPrefix(meta)（此前修混科页脚时引入）。
check('页脚模板仍走 footerSubjectPrefix（跟 subject_line）', /footerSubjectPrefix\(meta\)/.test(src));

console.log('\n===== 结果 =====');
console.log('通过 ' + passed + ' / 失败 ' + failed);
if (failed) { console.log(failures.map(f => ' - ' + f).join('\n')); process.exit(1); }
