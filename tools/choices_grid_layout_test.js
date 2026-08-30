// 回归测试：选择题选项网格的列数自适应（缺口 2）
//
// 问题：旧逻辑只在「内容溢出」时才降列。像 $P(X>2)>0.2$ 这类短选项在窄预览栏里
// 根本不溢出，于是稳稳保持 4 列，四个选项挤成一行，观感等同内联文本。
//
// 修复：除「不溢出」外，再要求每列保有最低可用宽度（MIN_CHOICES_COLUMN_PX=150），
// 窄容器自动回落 2 列，宽容器仍保留 4 列（贴合真实试卷观感）。
//
// 注：jsdom 不做真实布局，clientWidth 恒为 0，故需显式桩掉宽度后再调用自适应函数。

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('/Users/ccsssy/.workbuddy/binaries/node/workspace/node_modules/jsdom');

const ROOT = '/Users/ccsssy/WorkBuddy/2026-08-24-00-26-36/math-question-bank';
const html = fs.readFileSync(path.join(ROOT, 'static/index.html'), 'utf8');
const codes = ['api', 'editor', 'ocr', 'import', 'paper'].map(
  n => fs.readFileSync(path.join(ROOT, 'static/js', `${n}.js`), 'utf8')
);

const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://127.0.0.1:8000/', pretendToBeVisual: true });
const win = dom.window; const doc = win.document;
win.renderMathInElement = () => {};
win.marked = { parse: x => x || '' };
win.DOMPurify = { sanitize: x => x };
win.tailwind = { config: {} };
win.IntersectionObserver = class { observe(){} unobserve(){} disconnect(){} };
win.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} };
win.MathBankModal = { open(){}, close(){} };
for (const code of codes) {
  try { doc.body.appendChild(Object.assign(doc.createElement('script'), { textContent: code })); } catch (e) {}
}

const assert = (cond, label) => { console.log((cond ? '✅' : '❌') + ' ' + label); if (!cond) process.exitCode = 1; return cond; };

// 把内容渲染进一个容器，桩掉网格宽度后跑自适应，返回最终列数
function columnsFor(content, containerWidth) {
  const host = doc.createElement('div');
  host.innerHTML = win.parseMarkdownWithMath(win.normalizeChoiceOptions(content));
  doc.body.appendChild(host);
  const grid = host.querySelector('.choices-grid');
  if (!grid) return { columns: null, grid: null };
  Object.defineProperty(grid, 'clientWidth', { value: containerWidth, configurable: true });
  win.adaptChoicesGridLayout(host);
  return { columns: parseInt(grid.dataset.choiceColumns || '', 10), grid: grid };
}

const SHORT = '则（ ）A. $P(X>2)>0.2$ B. $P(X>2)<0.5$ C. $P(Y>2)>0.5$ D. $P(Y>2)<0.8$';
const LONG = '下列说法正确的是（ ）A. 若两个平面互相垂直，则一个平面内的任意一条直线都垂直于另一个平面内的所有直线，这是一个很长的选项 B. 另一个也很长的选项用于触发单列布局的阈值条件测试文本内容 C. 第三个同样较长的选项文本用于确保宽度超过二十四字符的阈值 D. 第四个较长的选项文本同样用于确保整体判定为单列显示';

console.log('=== 短选项（4 个纯公式，长度远低于阈值）===');
const narrow = columnsFor(SHORT, 420);
console.log('   容器 420px → 列数:', narrow.columns);
assert(narrow.columns === 2, '窄容器(420px)回落为 2 列，不再挤成一行');

const wide = columnsFor(SHORT, 800);
console.log('   容器 800px → 列数:', wide.columns);
assert(wide.columns === 4, '宽容器(800px)保留 4 列，贴合试卷观感');

const tiny = columnsFor(SHORT, 260);
console.log('   容器 260px → 列数:', tiny.columns);
assert(tiny.columns === 1, '极窄容器(260px)回落为 1 列');

console.log('\n=== 长选项（超阈值，首选即 1 列）===');
const longNarrow = columnsFor(LONG, 420);
console.log('   容器 420px → 列数:', longNarrow.columns);
assert(longNarrow.columns === 1, '长选项在窄容器为 1 列');

const longWide = columnsFor(LONG, 1200);
console.log('   容器 1200px → 列数:', longWide.columns);
assert(longWide.columns === 1, '长选项即使宽容器也保持 1 列（首选列数封顶）');

console.log('\n=== 端到端：内联选项经归一化后确实渲染成 choices 网格 ===');
const host = doc.createElement('div');
host.innerHTML = win.parseMarkdownWithMath(win.normalizeChoiceOptions(SHORT));
const grid = host.querySelector('.choices-grid');
assert(!!grid, '内联 A./B./C./D. 经归一化后渲染出 choices-grid');
assert((host.innerHTML.match(/choices-item/g) || []).length === 4, '渲染出 4 个选项条目');
assert(/choices-label[^>]*>A\./.test(host.innerHTML) && /choices-label[^>]*>D\./.test(host.innerHTML), '自动编号 A./D. 正确');

console.log('\n' + (process.exitCode ? 'FAIL: 选项网格列数自适应' : 'ALL PASS: 选项网格列数自适应'));
process.exit(process.exitCode || 0);
