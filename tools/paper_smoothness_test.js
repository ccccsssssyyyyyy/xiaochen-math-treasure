// 组卷虚拟滚动「丝滑度」量化验证
// 策略：在渲染函数定义后注入导出钩子，用 jsdom window.eval 在真实 window 上下文执行 paper.js
// 测量"丝滑度"代理指标：DOM 节点数是否随题库规模恒定、KaTeX 是否只渲染可视区、滚动重渲染是否恒定。
const fs = require('fs');
const { JSDOM } = require('jsdom');

const PAPER_JS = ROOT + '/static/js/paper.js';
let code = fs.readFileSync(PAPER_JS, 'utf8');

// 在"题目详情弹窗"注释前插入导出钩子（此时渲染函数与常量已定义，初始化代码尚未执行）
const marker = '// ---- 题目详情弹窗';
if (!code.includes(marker)) throw new Error('export marker not found');
code = code.replace(marker,
  `;window.__EXPORTS__ = {
    renderPart3QuestionStream, updatePaperVirtualList, getPaperDisplayList,
    renderPaperStreamCard, ensurePaperScrollBinding, paperVirtual,
    paperStreamHeaderHtml, switchPaperStreamTab,
    PAPER_ITEM_HEIGHT, PAPER_ITEM_GAP, PAPER_STRIDE, PAPER_OVERSCAN
  };\n` + marker);

// jsdom 环境（outside-only：手动 eval paper.js）
const dom = new JSDOM('<!DOCTYPE html><html><body><div id="paperQuestionStream"></div></body></html>', { url: 'http://localhost:8000/paper/', runScripts: 'outside-only' });
const win = dom.window;
const docEl = win.document;

// KaTeX 调用计数
let katexCalls = 0;

// 预置 paper.js 依赖的全局工具函数（真实浏览器里由 editor.js 等提供）
win.MathBankSafe = {
  safeClassList: (cls) => cls,
  safeSetHTML: (el, html) => { if (el) el.innerHTML = html; },
  escapeText: (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])),
  sanitizeRichHtml: (html) => html,
};
win.escapeHtml = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
win.getQuestionTypeCn = (t) => ({ single_choice:'单选题', multiple_choice:'多选题', fill_blank:'填空题', solution:'解答题', detailed_answer:'解答题' }[t] || t);
win.getDifficultyBadge = () => win.document.createElement('span');
win.getQuestionFigAlign = () => 'center';
win.formatQuestionContentHtml = (content) => `<div class="q">${content || ''}</div>`;
win.renderMathInElement = () => { katexCalls++; };
win.adaptChoicesGridLayout = () => {};
win.seedPaperAnswerCache = () => {};
win.hasCachedPaperAnswer = () => false;
win.isInCart = (id) => win.PaperStore.cart.some(it => it.id === id);
win.showToast = () => {};
win.switchPaperStreamTab = () => {};

// 让 #paperQuestionStream 有真实可视高度
const container = docEl.getElementById('paperQuestionStream');
Object.defineProperty(container, 'clientHeight', { configurable: true, get: () => 800 });
container.scrollTop = 0;

// 执行 paper.js（导出钩子会写 window.__EXPORTS__；初始化代码即使抛错也不影响已导出的函数）
try { win.eval(code); } catch (e) { /* ignore init errors after exports */ }
if (!win.__EXPORTS__) throw new Error('FAIL: 未能从 paper.js 导出渲染函数，可能结构已变');

const { renderPart3QuestionStream, updatePaperVirtualList, PAPER_STRIDE, PAPER_OVERSCAN } = win.__EXPORTS__;

// 构造题库
function makeQuestions(n) {
  const arr = [];
  for (let i = 1; i <= n; i++) {
    arr.push({ id: i, seq_num: i, question_type: i % 3 === 0 ? 'fill_blank' : 'single_choice', difficulty: 'medium', content: `<p>第 ${i} 题：若 $x+y=1$，求 $z=x^2+y^2$ 的最小值。</p>`, has_answer: true, usage_count: 0 });
  }
  return arr;
}
function setBank(n) {
  const qs = makeQuestions(n);
  const map = {};
  qs.forEach(q => map[q.id] = q);
  const ps = win.PaperStore;
  ps.cart = [];
  ps.bankQuestions = qs;
  ps.questionsMap = map;
  ps.filters = { tab: 'all' };
  ps.answerCache = {};
}
function countCards() {
  const vp = docEl.getElementById('paperVirtualViewport');
  return vp ? vp.children.length : 0;
}
function getSpacerHeight() {
  const sp = docEl.getElementById('paperVirtualSpacer');
  return sp ? parseInt(sp.style.height) : 0;
}

const scales = [493, 2000, 5000];
console.log('=== 组卷虚拟滚动丝滑度量化 ===');
console.log(`视口高度=800px, STRIDE=${PAPER_STRIDE}px, OVERSCAN=${PAPER_OVERSCAN}`);
console.log('规模\t\t首屏卡片数\tKaTeX(首屏)\tspacer(px)\t滚动后卡片数\t滚动KaTeX增量\t重建耗时(ms)');
const rows = [];
for (const n of scales) {
  setBank(n);
  katexCalls = 0;
  container.scrollTop = 0;
  const t0 = Date.now();
  renderPart3QuestionStream({ resetScroll: true });
  const rebuildMs = Date.now() - t0;
  const firstCards = countCards();
  const firstKatex = katexCalls;
  const spacer = getSpacerHeight();

  container.scrollTop = Math.floor(n / 3) * PAPER_STRIDE;
  const before = katexCalls;
  updatePaperVirtualList();
  const scrolledCards = countCards();
  const delta = katexCalls - before;

  console.log(`${n}\t\t${firstCards}\t\t${firstKatex}\t\t${spacer}\t\t${scrolledCards}\t\t${delta}\t\t${rebuildMs}`);
  rows.push({ n, firstCards, firstKatex, spacer, scrolledCards, delta, rebuildMs });

  if (firstKatex !== firstCards) throw new Error(`FAIL: KaTeX 首屏调用 ${firstKatex} != 卡片数 ${firstCards}`);
  if (delta !== scrolledCards) throw new Error(`FAIL: 滚动 KaTeX 增量 ${delta} != 可视卡片 ${scrolledCards}`);
  if (spacer !== n * PAPER_STRIDE) throw new Error(`FAIL: spacer ${spacer} != ${n*PAPER_STRIDE}`);
  // 丝滑核心：视口内卡片数必须远小于题库规模（否则即全量渲染卡顿）
  if (firstCards > n / 5 || firstCards > 50) throw new Error(`FAIL: 首屏卡片 ${firstCards} 过多，未达虚拟化`);
  if (scrolledCards > n / 5 || scrolledCards > 50) throw new Error(`FAIL: 滚动卡片 ${scrolledCards} 过多`);
}

// 关键断言：卡片数只取决于视口高度，与题库规模无关（规模间应一致）
const firstSet = new Set(rows.map(r => r.firstCards));
const scrolledSet = new Set(rows.map(r => r.scrolledCards));
if (firstSet.size !== 1) throw new Error(`FAIL: 首屏卡片数随规模变化 ${[...firstSet]}（应恒定）`);
if (scrolledSet.size !== 1) throw new Error(`FAIL: 滚动卡片数随规模变化 ${[...scrolledSet]}（应恒定）`);

// 切 tab 到 selected（已选 11 题）：验证昨天修复的 displayList ReferenceError 不再发生，且虚拟化正常
setBank(100);
win.PaperStore.cart = [1,2,3,4,5,6,7,8,9,10,11].map(id => ({ id, score: 5 }));
win.PaperStore.filters.tab = 'selected';
katexCalls = 0;
renderPart3QuestionStream({ resetScroll: true });
const selectedCards = countCards();
console.log(`\n已选试题 tab (共11题): 首屏渲染卡片数=${selectedCards}, KaTeX=${katexCalls}`);
if (selectedCards < 1 || selectedCards > 11) throw new Error(`FAIL: 已选 tab 首屏渲染 ${selectedCards} 张，应在 (0,11]`);
if (katexCalls !== selectedCards) throw new Error(`FAIL: 已选 tab KaTeX ${katexCalls} != 卡片 ${selectedCards}`);
// 滚动后能看到全部 11 张
container.scrollTop = 10 * PAPER_STRIDE;
let seen = new Set();
for (let s = 0; s <= 10 * PAPER_STRIDE; s += PAPER_STRIDE) {
  container.scrollTop = s;
  updatePaperVirtualList();
  const vp = docEl.getElementById('paperVirtualViewport');
  for (const card of vp.children) {
    const m = card.innerHTML.match(/#(\d+)</);
    if (m) seen.add(parseInt(m[1]));
  }
}
console.log(`滚动遍历后可见题号数=${seen.size} (期望=11)`);
if (seen.size !== 11) throw new Error(`FAIL: 滚动后仅见 ${seen.size} 题，期望 11`);

console.log('\nPASS: 组卷虚拟滚动 DOM 节点数只取决于视口高度、与题库规模无关(493/2000/5000 首屏均='
  + [...firstSet] + '、滚动均=' + [...scrolledSet] + ')，KaTeX 仅渲染可视区，已选 tab 切换正常且可滚动遍历全部题。');
