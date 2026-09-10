/**
 * 选择题选项标号去重 —— 前端真实执行校验。
 *
 * 回归背景：
 *     Word 拆解结果里选项正文常带残留显式标号（`\item A. $\frac{8\pi}{5}$`），
 *     而预览渲染时 choices 环境会自动补 A./B./C./D. 标签，两者叠加渲染成
 *     「A. A. 8π/5」这类重复标号。
 *
 * 本脚本从 editor.js 中按行切片抠出 preprocessFormulaForKaTeX，在 vm 沙箱里
 * 真实执行（外部依赖自动 identity 打桩），校验剥离逻辑与公式保留。
 *
 * 用法: node tests/js/choices_label_dedupe_check.js [editor.js 路径]
 * 退出码: 0 全通过 / 1 有失败
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const editorPath = process.argv[2]
  || path.join(__dirname, '..', '..', 'static', 'js', 'editor.js');

const src = fs.readFileSync(editorPath, 'utf8');
const lines = src.split('\n');

const startIdx = lines.findIndex(l => l.includes('function preprocessFormulaForKaTeX(text)'));
const endMarker = lines.findIndex((l, i) => i > startIdx
  && l.includes('window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX'));
if (startIdx < 0 || endMarker < 0) {
  console.error('LOCATE FAIL: 未找到 preprocessFormulaForKaTeX 边界');
  process.exit(1);
}
const fnSrc = lines.slice(startIdx, endMarker).join('\n');

// 自动补桩：缺失的外部依赖一律 identity，直到跑通
const ctx = { console };
function run(input) {
  for (let i = 0; i < 30; i++) {
    try {
      const sandbox = Object.assign({}, ctx, { __in: input });
      vm.runInNewContext(fnSrc + '\n;__out = preprocessFormulaForKaTeX(__in);',
        sandbox, { filename: 'preprocessFormulaForKaTeX.slice.js' });
      return sandbox.__out;
    } catch (e) {
      // 跨 realm 的 instanceof 不可靠，按 name 判定
      if (e && (e.name === 'ReferenceError' || e.constructor.name === 'ReferenceError')) {
        const m = /(\w+) is not defined/.exec(e.message);
        if (m) { ctx[m[1]] = (x) => x; continue; }
      }
      throw e;
    }
  }
  throw new Error('stub loop exhausted');
}

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}

// 注意：choices-content 的 class 里含 `>` 字符（[&>p]:m-0），断言不要用 [^>]* 截断
console.log('=== 用例1：带残留标号的选项（Word 拆解典型形态）===');
const t1 = '已知扇形的圆心角为(  )\n\\begin{choices}\n\\item A. $\\frac{8\\pi}{5}$\n'
  + '\\item B. $\\frac{4\\pi}{5}$\n\\item C. $\\frac{2\\pi}{5}$\n\\item D. $\\frac{\\pi}{5}$\n\\end{choices}';
const o1 = run(t1);
check('四个选项都生成 choices-content', (o1.match(/choices-content/g) || []).length === 4);
['A', 'B', 'C', 'D'].forEach((L) => {
  check(`${L} 选项正文不再残留 "${L}." 前缀`, !o1.includes(`[&>p]:inline">${L}.`));
});
check('A 选项公式完整保留', o1.includes('$\\frac{8\\pi}{5}$'));
check('D 选项公式完整保留', o1.includes('$\\frac{\\pi}{5}$'));
check('自动编号标签仍在', /choices-label[^>]*>A\./.test(o1) && /choices-label[^>]*>D\./.test(o1));

console.log('\n=== 用例2：全角括号形态残留标号 ===');
const o2 = run('\\begin{choices}\n\\item （A）$\\frac{1}{2}$\n\\item （B）$2$\n'
  + '\\item （C）$3$\n\\item （D）$4$\n\\end{choices}');
check('（A）形态被剥离', !o2.includes('[&>p]:inline">（A）'));
check('（B）形态被剥离', !o2.includes('[&>p]:inline">（B）'));
check('公式保留', o2.includes('$\\frac{1}{2}$'));

console.log('\n=== 用例3：无残留标号的正常输入（回归，不得误删）===');
const o3 = run('\\begin{choices}\n\\item $\\frac{8\\pi}{5}$\n\\item $\\frac{4\\pi}{5}$\n'
  + '\\item $2$\n\\item $4$\n\\end{choices}');
check('正常输入公式不受损', o3.includes('$\\frac{8\\pi}{5}$'));
check('正常输入选项数正确', (o3.match(/choices-content/g) || []).length === 4);

console.log('\n=== 用例4：公式未加 $ 时自动包裹，且不把 A. 包进数学模式 ===');
const o4 = run('\\begin{choices}\n\\item A. \\frac{8\\pi}{5}\n\\item B. 4\n\\end{choices}');
check('自动包裹为 $...$', o4.includes('>$\\frac{8\\pi}{5}$<'));
check('包裹内容不含 A. 文本', !o4.includes('>$A.') && !o4.includes('$\\A.'));

console.log('\n=== 用例5：以字母开头的合法选项正文不被误删（如 $x>0$）===');
// 既有契约：数学模式内的 < > 会先被转义为 \lt / \gt（editor.js 2112 行附近）
const o5 = run('\\begin{choices}\n\\item $x>0$\n\\item $x<0$\n\\end{choices}');
check('$x>0$ 未误删且按既有契约转义', o5.includes('$x\\gt 0$'), '实际: ' + o5.slice(0, 200));

console.log('\n==============================');
console.log(`结果: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
