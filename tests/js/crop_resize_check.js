/**
 * PDF/Word 手动截图：选区缩放算法校验（computeCropResize 真实执行）。
 *
 * 回归背景：
 *     原实现只能一次性拖拽画框，画完再按下去就等于重画，无法调整大小。
 *     现支持四角手柄缩放 + 整框平移，且约定「不翻越」——拖过对角锚点时
 *     框停在最小尺寸，绝不翻转到另一侧（用户明确要求）。
 *
 * 本脚本从 import.js 中抠出纯函数 computeCropResize，在 vm 沙箱里真实执行，
 * 校验四个方向的缩放与越界 clamping。
 *
 * 用法: node tests/js/crop_resize_check.js [import.js 路径]
 * 退出码: 0 全通过 / 1 有失败
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const srcPath = process.argv[2]
  || path.join(__dirname, '..', '..', 'static', 'js', 'import.js');

const src = fs.readFileSync(srcPath, 'utf8');

// 大括号计数切片：该函数体内无正则字面量，计数安全
const fnStart = src.indexOf('function computeCropResize(');
if (fnStart < 0) {
  console.error('LOCATE FAIL: 未找到 computeCropResize');
  process.exit(1);
}
let depth = 0;
let end = -1;
for (let i = src.indexOf('{', fnStart); i < src.length; i++) {
  if (src[i] === '{') depth++;
  else if (src[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
}
if (end < 0) { console.error('BRACE FAIL'); process.exit(1); }
const fnSrc = src.slice(fnStart, end);

const sandbox = { console };
vm.runInNewContext(fnSrc + '\n;__fn = computeCropResize;', sandbox, { filename: 'computeCropResize.slice.js' });
const resize = sandbox.__fn;

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}
function eq(name, got, want) {
  const ok = Math.abs(got - want) < 0.001;
  check(name, ok, `期望 ${want}，实际 ${got}`);
}

const MIN = 15;
const MAXW = 1000;
const MAXH = 800;

console.log('=== 用例1：se 角向右下拖大（nw 锚点固定）===');
let r = resize('se', 100, 100, 400, 300, MAXW, MAXH, MIN);
eq('left 固定为锚点', r.left, 100);
eq('top 固定为锚点', r.top, 100);
eq('width', r.width, 300);
eq('height', r.height, 200);

console.log('\n=== 用例2：se 角向左上拖小 ===');
r = resize('se', 100, 100, 200, 150, MAXW, MAXH, MIN);
eq('width', r.width, 100);
eq('height', r.height, 50);

console.log('\n=== 用例3：se 角越过锚点 —— 不翻越，停在最小尺寸 ===');
r = resize('se', 100, 100, 50, 40, MAXW, MAXH, MIN);
eq('left 仍等于锚点（未翻转）', r.left, 100);
eq('top 仍等于锚点（未翻转）', r.top, 100);
eq('width 收敛到 minSize', r.width, MIN);
eq('height 收敛到 minSize', r.height, MIN);

console.log('\n=== 用例4：nw 角向左上拖大（se 锚点固定）===');
r = resize('nw', 400, 300, 100, 100, MAXW, MAXH, MIN);
eq('left', r.left, 100);
eq('top', r.top, 100);
eq('width', r.width, 300);
eq('height', r.height, 200);
eq('右下边界贴合锚点', r.left + r.width, 400);
eq('右下边界贴合锚点(y)', r.top + r.height, 300);

console.log('\n=== 用例5：nw 角拖过锚点 —— 不翻越 ===');
r = resize('nw', 400, 300, 500, 400, MAXW, MAXH, MIN);
eq('width 收敛到 minSize', r.width, MIN);
eq('height 收敛到 minSize', r.height, MIN);
check('未翻转到锚点右侧（left < 锚点）', r.left < 400, `left=${r.left}`);
check('未翻转到锚点下方（top < 锚点）', r.top < 300, `top=${r.top}`);
eq('右下边界仍贴合锚点', r.left + r.width, 400);

console.log('\n=== 用例6：ne 角（sw 锚点固定）===');
r = resize('ne', 100, 300, 400, 100, MAXW, MAXH, MIN);
eq('left 固定', r.left, 100);
eq('top', r.top, 100);
eq('width', r.width, 300);
eq('height', r.height, 200);

console.log('\n=== 用例7：ne 角越过下边界 —— 不翻越 ===');
r = resize('ne', 100, 300, 400, 400, MAXW, MAXH, MIN);
eq('height 收敛到 minSize', r.height, MIN);
check('未翻转到锚点上方（top < 锚点）', r.top < 300, `top=${r.top}`);
eq('下边界仍贴合锚点', r.top + r.height, 300);

console.log('\n=== 用例8：sw 角（ne 锚点固定）===');
r = resize('sw', 400, 100, 100, 300, MAXW, MAXH, MIN);
eq('left', r.left, 100);
eq('top 固定', r.top, 100);
eq('width', r.width, 300);
eq('height', r.height, 200);

console.log('\n=== 用例9：sw 角越过右边界 —— 不翻越 ===');
r = resize('sw', 400, 100, 500, 300, MAXW, MAXH, MIN);
eq('width 收敛到 minSize', r.width, MIN);
check('未翻转到锚点右侧（left < 锚点）', r.left < 400, `left=${r.left}`);
eq('右边界仍贴合锚点', r.left + r.width, 400);

console.log('\n=== 用例10：拖出容器右下角 —— clamp 在容器内 ===');
r = resize('se', 900, 700, 2000, 1500, MAXW, MAXH, MIN);
eq('width 被夹到容器剩余宽度', r.width, 100);
eq('height 被夹到容器剩余高度', r.height, 100);
check('不超出右边界', r.left + r.width <= MAXW, `${r.left + r.width}`);
check('不超出下边界', r.top + r.height <= MAXH, `${r.top + r.height}`);

console.log('\n=== 用例11：拖到负坐标 —— clamp 到 0 ===');
r = resize('nw', 200, 200, -500, -500, MAXW, MAXH, MIN);
eq('left 夹到 0', r.left, 0);
eq('top 夹到 0', r.top, 0);
eq('width 相应变大', r.width, 200);
eq('height 相应变大', r.height, 200);

console.log('\n=== 用例12：四种 handle 均返回非负宽高 ===');
['nw', 'ne', 'sw', 'se'].forEach((h) => {
  const box = resize(h, 300, 300, 0, 0, MAXW, MAXH, MIN);
  check(`${h} 宽高非负`, box.width >= 0 && box.height >= 0,
    `w=${box.width} h=${box.height}`);
});

console.log('\n==============================');
console.log(`结果: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
