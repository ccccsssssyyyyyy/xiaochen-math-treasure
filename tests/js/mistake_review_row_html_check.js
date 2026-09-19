/**
 * 审校页左列表行结构 + 事件传播 —— 真实 DOM（jsdom）核对。
 *
 * 分层理由：`mistake_review_payload_check.js` 用的是手搓假 DOM，只能断言 innerHTML
 * 字符串里写了什么。而这一行有两个**只有真浏览器才暴露**的错法：
 *
 *   1) `<button>` 里套 `<button>`。HTML 上这是非法嵌套，解析器会把内层按钮**提到行
 *      外面**变成兄弟节点 —— 字符串里两个标签都在、顺序也对，看起来毫无问题，但屏幕
 *      上那个 × 已经不在行里了（排版错位、点不准）。2026-09-18 这次改成内联 × 时，
 *      行的外层必须从 button 换成 div 正是为了这个。
 *   2) 内层按钮的 stopPropagation 是否真的挡住了行上的选中。假 DOM 里没有冒泡，
 *      这条只能靠真实事件派发验。
 *
 * 所以这里做两件事：把 fixture 真实生成的行 HTML 落盘 → 用 jsdom 解析 → 断言嵌套
 * 形状（× 是行的后代、且全行只有它一个按钮）→ 往 × 上派发真实 click，确认只调
 * removeMistakeReviewRecord、没顺带调 selectMistakeReviewRecord。
 *
 * 用法: node tests/js/mistake_review_row_html_check.js [mistake.js 路径]
 * 退出码: 0 全通过 / 1 有失败 / 2 环境不具备（缺 jsdom、fixture 跑不通）
 */
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

let JSDOM;
try {
  ({
    JSDOM
  } = require('jsdom'));
} catch (err) {
  console.log('环境不具备：未找到 jsdom（NODE_PATH 没带 workspace/node_modules）。');
  process.exit(2);
}

const jsPath = process.argv[2]
  || path.join(__dirname, '..', '..', 'static', 'js', 'mistake.js');
const fixture = path.join(__dirname, 'mistake_review_payload_check.js');

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}

// ---------------------------------------------------------------- 取真实行 HTML

const dumpPath = path.join(os.tmpdir(), 'mr_row_html_' + process.pid + '.html');
try {
  execFileSync(process.execPath, [fixture, jsPath], {
    env: Object.assign({}, process.env, { MR_ROW_HTML_OUT: dumpPath }),
    stdio: ['ignore', 'ignore', 'inherit'],
    timeout: 120000
  });
} catch (err) {
  console.log('环境不具备：跑不通 mistake_review_payload_check.js（' + err.message + '）。');
  process.exit(2);
}
if (!fs.existsSync(dumpPath)) {
  console.log('环境不具备：夹具没有产出 ' + dumpPath + '（入口没走到 [18] ？）。');
  process.exit(2);
}
const rowHtml = fs.readFileSync(dumpPath, 'utf8');
fs.unlinkSync(dumpPath);

// ---------------------------------------------------------------- 真实 DOM 解析
// runScripts: 'dangerously' 才会编译内联 onclick 属性 —— 这一层同时验「属性写对了」
// 与「函数挂在 window 上」，缺一个都会在派发时报 ReferenceError 而不是静默跳过。

const dom = new JSDOM('<!doctype html><html><body><div id="mrList"></div></body></html>', {
  runScripts: 'dangerously',
  pretendToBeVisual: true
});
const { window: win } = dom;
const doc = win.document;
doc.getElementById('mrList').innerHTML = rowHtml;

const rows = doc.querySelectorAll('#mrList > [role="button"]');
check('每行都解析成一个 role=button 的行节点', rows.length >= 1, 'rows=' + rows.length);

const row = rows[0];
// ① 嵌套形状：全行只允许一个按钮（那个 ×），且必须是行的后代。
//    若外层还是 button，解析器会把内层提出去 —— 内层按钮就不再是行的后代了。
const buttonsInRow = row.querySelectorAll('button');
check('行内恰好一个按钮（那个 ×）', buttonsInRow.length === 1, 'count=' + buttonsInRow.length);
check('× 是行的后代（不是被解析器提出去的兄弟节点）',
  buttonsInRow.length === 1 && row.contains(buttonsInRow[0]));
const topLevelButtons = Array.from(doc.querySelectorAll('#mrList > button'));
check('行外层不是 button（只有 div[role=button]）', topLevelButtons.length === 0,
  topLevelButtons.map(function (b) { return b.outerHTML.slice(0, 60); }).join(' | '));

const removeBtn = buttonsInRow[0];
check('× 上的内联 handler 编译成功且挂在 window 上（没有 ReferenceError）',
  /event\.stopPropagation\(\);removeMistakeReviewRecord\(\d+\)/.test(removeBtn.getAttribute('onclick') || ''),
  removeBtn.getAttribute('onclick'));

// ② 事件传播：派发真实 click，只该触发「移出」，不该顺带触发「选中」。
const calls = [];
win.removeMistakeReviewRecord = function (id) { calls.push(['remove', id]); };
win.selectMistakeReviewRecord = function (id) { calls.push(['select', id]); };

const removeId = Number(/removeMistakeReviewRecord\((\d+)\)/.exec(removeBtn.getAttribute('onclick'))[1]);
const rowId = Number(/selectMistakeReviewRecord\((\d+)\)/.exec(row.getAttribute('onclick'))[1]);
removeBtn.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));

check('点 × 调到了「移出本次收录」，且带的是本行的记录 id',
  calls.some(function (c) { return c[0] === 'remove' && c[1] === removeId; }), JSON.stringify(calls));
check('点 × 没有顺带触发「选中」（stopPropagation 真的挡住了冒泡）',
  !calls.some(function (c) { return c[0] === 'select'; }), JSON.stringify(calls));

// 反向对照：点行体本身仍然要能选中 —— 别把整行点不动了当成「修好了」。
calls.length = 0;
row.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
check('点行体本身依然选中这道题', calls.length === 1 && calls[0][0] === 'select' && calls[0][1] === rowId,
  JSON.stringify(calls));

console.log('\n' + (fail === 0 ? '全部通过：' + pass + ' 项' : '失败 ' + fail + ' 项 / 共 ' + (pass + fail) + ' 项'));
process.exit(fail === 0 ? 0 : 1);
