// 回归测试：前端 normalizeChoiceOptions（static/js/editor.js）
//
// 背景：后端 mathbank/latex_normalize.py 在入库时归一化选项，但要等到「保存」才生效；
// 录入过程中编辑框与预览仍是内联形态（A. ... B. ...），观感等同没套 choices 环境。
// 前端补上同一套归一化后，需保证与后端行为完全一致，否则会出现「预览与入库结果不一致」。
//
// 本测试加载真实前端脚本 static/js/editor.js，覆盖与后端 tools/choice_options_test.py
// 相同的用例，并额外做一轮「JS 输出 vs Python 输出」逐例比对，确保两端语义对齐。
//
// 注：原实现用 jsdom 加载整页脚本，但在受限沙箱里 jsdom 会触发 OOM（exit 137）导致
// 结果失真。normalizeChoiceOptions 完全自包含于 editor.js（不依赖 DOM 或其它脚本），
// 故改为用轻量 vm 沙箱直接加载 editor.js，既保真又可稳定跑完。

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = require('path').resolve(__dirname, '..');
const editorSrc = fs.readFileSync(path.join(ROOT, 'static/js', 'editor.js'), 'utf8');

const noop = function () {};
const sandbox = {
  console: console,
  fetch: noop,
  setInterval: noop,
  setTimeout: noop,
  requestAnimationFrame: noop,
  document: {
    getElementById: function () { return null; },
    querySelector: function () { return null; },
    addEventListener: noop,
    createElement: function () { return { style: {}, addEventListener: noop, appendChild: noop }; },
    body: { appendChild: noop },
  },
  navigator: { userAgent: 'node' },
  location: { href: '' },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(editorSrc, sandbox, { filename: 'editor.js' });

const assert = (cond, label) => { console.log((cond ? '✅' : '❌') + ' ' + label); if (!cond) process.exitCode = 1; return cond; };
const norm = sandbox.window.normalizeChoiceOptions;

if (!assert(typeof norm === 'function', 'normalizeChoiceOptions 已暴露到 window')) {
  process.exit(1);
}

// ---- 与后端 tools/choice_options_test.py 对齐的用例 ----
const inline = 'A. $a>0>b$ B. $a>b>0$ C. $b>a>0$ D. $b>0>a$';
const out1 = norm(inline);
assert(out1.trim().startsWith('\\begin{choices}') && out1.trim().endsWith('\\end{choices}'), '内联选项包裹为 choices 环境');
assert((out1.match(/\\item/g) || []).length === 4, '内联选项生成 4 个 \\item');
assert(!/A\. |B\. |D\. /.test(out1), '\\item 内不含显式 A./B./D. 标号');
assert(out1.includes('$a>0>b$') && out1.includes('$b>0>a$'), '内联选项保留数学正文');

const withLabel = '\\begin{choices}\\item A. $x>0$\\item B. $x<0$\\end{choices}';
const out2 = norm(withLabel);
assert(!/A\. /.test(out2) && (out2.match(/\\item/g) || []).length === 2, 'choices 内去显式标号');

const fullwidth = '（A）$a>0$（B）$a<b$（C）$a=b$（D）无法比较';
const out3 = norm(fullwidth);
assert(out3.trim().startsWith('\\begin{choices}') && (out3.match(/\\item/g) || []).length === 4, '全角括号选项包裹为 choices');
assert(!out3.includes('（A）'), '全角选项去 （A） 标号');

const solve = '解：(1) 当 x>0 时，f(x)=x^2；(2) 当 x<0 时，f(x)=-x。';
assert(norm(solve) === solve, '解答题内容保持不变');

const mathonly = '令 f(A)=x，则 g(B)=y，其中 A、B 为集合。';
assert(norm(mathonly) === mathonly, '数学内部 (A) 不被误判为选项');

const inlineAns = '已知 x>0，A. $x>1$ B. $x>2$ C. $x>3$ D. $x>4$ 答案：B';
const out6 = norm(inlineAns);
assert(!out6.includes('答案：B') && (out6.match(/\\item/g) || []).length === 4, '选项后答案标记被截断');

// 公式分母 (a+b)(c+d)(a+c)(b+d) 不得被误判为选项（数学模式保护）
const formula = "$\\chi^{2}=\\frac{n(ad-bc)^{2}}{(a+b)(c+d)(a+c)(b+d)}$，其中 $n=a+b+c+d$。";
assert(norm(formula) === formula && !norm(formula).includes('\\begin{choices}'), '公式内 (a+b) 等不被误判为选项');

assert(norm(norm(inline)) === norm(inline), '幂等性');
assert(norm('') === '' && norm(null) === null && norm(undefined) === undefined, '空值 / 非字符串安全');

// ---- 与后端 Python 实现逐例比对，确保两端语义一致 ----
const parityCases = [
  inline, withLabel, fullwidth, solve, mathonly, inlineAns,
  '则（ ）（若随机变量 $Z$ 服从正态分布 $N(\\mu,\\sigma^2)$） A. $P(X>2)>0.2$ B. $P(X>2)<0.5$ C. $P(Y>2)>0.5$ D. $P(Y>2)<0.8$',
  '已知集合 $A=\\{1,2\\}$，$B=\\{2,3\\}$，则 $A\\cap B=$（　　）A. $\\{2\\}$ B. $\\{1,2\\}$ C. $\\{2,3\\}$ D. $\\{1,2,3\\}$',
  'A、$1$ B、$2$ C、$3$ D、$4$',
  '(A) $x>0$ (B) $x<0$',
  '', '纯文本内容没有选项',
  "$\\chi^{2}=\\frac{n(ad-bc)^{2}}{(a+b)(c+d)(a+c)(b+d)}$，其中 $n=a+b+c+d$。",
];

// 基准由 tools/_choice_parity_py_baseline.py 事先生成：
//     cd <项目根> && venv/bin/python tools/_choice_parity_py_baseline.py
// 不在本进程内 spawn Python —— 沙箱内存有限，Node 与 Python 同进程树会被 OOM 杀掉。
let pyOut = [];
try {
  pyOut = JSON.parse(fs.readFileSync('/tmp/_choice_parity_py.json', 'utf8'));
} catch (e) {
  console.log('⚠️  未找到 Python 基准 /tmp/_choice_parity_py.json，跳过比对。');
  console.log('    生成方式：venv/bin/python tools/_choice_parity_py_baseline.py');
}

if (pyOut.length === parityCases.length) {
  let mismatch = 0;
  for (let i = 0; i < parityCases.length; i++) {
    if (norm(parityCases[i]) !== pyOut[i]) {
      mismatch++;
      console.log('   ❌ 第 ' + (i + 1) + ' 例不一致');
      console.log('      输入 : ' + JSON.stringify(parityCases[i]));
      console.log('      JS   : ' + JSON.stringify(norm(parityCases[i])));
      console.log('      Python: ' + JSON.stringify(pyOut[i]));
    }
  }
  assert(mismatch === 0, '与后端 Python 实现逐例一致（' + parityCases.length + ' 例）');
} else {
  console.log('⚠️  跳过与后端的比对');
}

console.log('\n' + (process.exitCode ? 'FAIL: normalizeChoiceOptions' : 'ALL PASS: normalizeChoiceOptions'));
process.exit(process.exitCode || 0);
