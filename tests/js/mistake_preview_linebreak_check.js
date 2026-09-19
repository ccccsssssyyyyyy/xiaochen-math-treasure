/**
 * 审校页右栏「题目预览」换行校验 —— 真实执行 mistake.js，不靠正则猜源码。
 *
 * 2026-09-18 用户反馈：预览把题干、(1)、(2) 糊成一整段，「右边的排版没有换行」。
 * 根因是 contentPreviewHtml 只做 esc + fillin/paren 替换，从不处理源文本里的 \n。
 * 修复约定：\n\n（及以上）是段落间隙（渲染成定高块），单个 \n 是折行（<br>）。
 *
 * 这里把真实函数跑起来，断言：
 *   [1] 题干 / (1) / (2) 之间必须断开（段落间隙 + <br> 都算断）；
 *   [2] choices 环境抽走后不能把 \n 泄漏成可见空隙或断在选项块里；
 *   [3] \fillin / \paren 的行内替换不受换行处理影响；
 *   [4] 正文内联图、占位符标记在换行转换后仍能被识别渲染；
 *   [5] 空串/纯空白不产生多余的段落块。
 *
 * 反向对照：`node tests/js/mistake_preview_linebreak_check.js .merge-rect-backup-20260918/mistake.js.bak`
 * —— 备份是没有换行处理的旧版，[1] 段必须成片变红，否则断言等于没测。
 *
 * 用法: node tests/js/mistake_preview_linebreak_check.js [mistake.js 路径]
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
function section(title) { console.log('\n' + title); }

// ---------------------------------------------------------------- 假 DOM（与 mistake_merge_rects_check 同一套最小面）

function makeElement(id) {
  const classes = new Set();
  return {
    id: id,
    innerHTML: '',
    textContent: '',
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

const sandbox = {
  console: console,
  setTimeout: function () { return 1; },
  clearTimeout: function () {},
  setInterval: function () { return 0; },
  clearInterval: function () {},
  fetch: function () {
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ status: 'success' }); } });
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
require('./sandbox_base').loadBaseModules(sandbox);
try {
  vm.runInContext(src, sandbox, { filename: 'mistake.js' });
} catch (err) {
  console.log('  FAIL  mistake.js 在沙箱里加载失败: ' + err.message);
  process.exit(1);
}

const preview = sandbox.mrContentPreviewHtml;
if (typeof preview !== 'function') {
  console.log('  FAIL  window.mrContentPreviewHtml 未暴露（旧版文件跑反向对照时预期如此）');
  console.log('\n通过 0 项，失败 1 项');
  process.exit(1);
}

// ---------------------------------------------------------------- 工具

const PARA = '<span class="block h-2"></span>';

/** 把渲染结果按「可见文本行」切开：段落块与 <br> 都算断行点（全局替换，不是只换首个） */
function lines(html) {
  return html
    .split(PARA).join('\n')
    .split(/<br>|\n/)
    .map(function (s) { return s.replace(/<[^>]+>/g, '').trim(); })
    .filter(function (s) { return s.length > 0; });
}

// ---------------------------------------------------------------- [1] 题干/(1)/(2) 断行

section('[1] 多段题干：段落间隙 + 折行');

const content1 = '如图所示是用运动传感器测小车速度的原理图，测量时 A 向 B 同时发射脉冲。\n\n（1）求开始计时时 A 与 B 之间的距离为 $x_1$；\n\n（2）经过 $\\Delta t$ 时间后，求小车运动的速度 $v$ 为多大？';
const html1 = preview(content1, []);
check('题干、(1)、(2) 分成三行，不再糊成一段', lines(html1).length === 3,
  'lines=' + JSON.stringify(lines(html1)));
check('段落间隙用的是定高块 ' + PARA.trim(), html1.indexOf(PARA) >= 0);
check('行内公式占位保持原样（交给 KaTeX）', html1.indexOf('$x_1$') >= 0 && html1.indexOf('$\\Delta t$') >= 0);

const content1b = '第一行末尾没有标点\n第二行紧跟其后';
const html1b = preview(content1b, []);
check('单个 \\n 渲染成 <br> 折行', html1b.indexOf('<br>') >= 0 && lines(html1b).length === 2,
  'lines=' + JSON.stringify(lines(html1b)));

// ---------------------------------------------------------------- [2] choices 环境不泄漏 \n

section('[2] choices 环境：抽走后不留裸换行');

const content2 = '下列说法正确的是：\n\n\\begin{choices}\n\\item 甲\n\\item 乙\n\\item 丙\n\\item 丁\n\\end{choices}';
const html2 = preview(content2, []);
check('choices 抽走后题干干净收尾（选项块自带 mt 间距，不靠段落块垫）', html2.indexOf(PARA) === -1);
check('题干里没有 <br>（换行残片不泄漏进正文）', html2.indexOf('<br>') === -1);
check('选项逐条渲染 A./B./C./D.', ['甲', '乙', '丙', '丁'].every(function (t) { return html2.indexOf(t) >= 0; }));
check('选项块之前没有裸露的换行残片', html2.indexOf('\n') === -1);

// ---------------------------------------------------------------- [3] fillin / paren 不受影响

section('[3] fillin / paren 行内替换');

const content3 = '在横线上填空：$\\fillin$，再括号内填 $\\paren$。';
const html3 = preview(content3, []);
check('\\fillin 仍渲染成下划线空位', html3.indexOf('border-b border-slate-400') >= 0);
check('\\paren 仍渲染成括号空位', html3.indexOf('（&nbsp;') >= 0);
check('行内替换后没有引入多余断行', html3.indexOf('<br>') === -1 && html3.indexOf(PARA) === -1);

// ---------------------------------------------------------------- [4] 内联图与占位符在换行转换后仍被识别

section('[4] 内联图 / 占位符标记');

const content4 = '题面文字在前。\n\n![插图](/static/uploads/mistakes/1/figures/crop_x.png)\n\n[插图待补: 图2]';
const html4 = preview(content4, []);
check('内联 markdown 图在 <br> 包夹下仍渲染成 <img>', /<img src="/.test(html4));
check('没有老图可配的占位符仍渲染成不可点标记（琥珀 chip）',
  html4.indexOf('插图待补') >= 0 && html4.indexOf('border-amber-300') >= 0);
check('图前后的换行变成了结构断行而不是裸 \\n', html4.indexOf('\n') === -1);

const content4b = '题面。\n\n[插图待补: 图1]';
const html4b = preview(content4b, ['/static/uploads/mistakes/1/figures/real.png']);
check('占位符按出现顺序配上老图时直接出图', /<img src=".*real\.png/.test(html4b));

// ---------------------------------------------------------------- [5] 空串与纯空白

section('[5] 空输入不产生多余结构');

check('空串返回空', preview('', []) === '');
check('纯空白返回空', preview('   \n\n  ', []) === '');
check('首尾空白被裁掉（不产生开头/结尾的断行结构）',
  preview('\n\n首段\n\n', []).indexOf(PARA) === -1);

// ---------------------------------------------------------------- 汇总

console.log('\n通过 ' + pass + ' 项，失败 ' + fail + ' 项');
process.exit(fail ? 1 : 0);
