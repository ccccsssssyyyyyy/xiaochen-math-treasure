/**
 * 工作台 tab 高亮的运行时校验 —— 真实执行 api.js 里的 selectWorkspace。
 *
 * 为什么要有这个脚本：
 *   三个工作台现在有两套入口（宽屏平铺 tab / 窄屏下拉），高亮由同一个
 *   selectWorkspace 同步。写漏一处就会出现「点了组卷、tab 还亮着题库」这种
 *   静默不一致 —— 静态断言只能证明字段名写对了，证明不了状态真的切过去了。
 *   所以这里把函数的真实源码抽出来在假 DOM 里跑一遍，驱动完整的三次切换。
 *
 * 用法: node tests/js/workspace_tabs_check.js [api.js 路径]
 * 退出码: 0 全通过 / 1 有失败
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const apiPath = process.argv[2]
  || path.join(__dirname, '..', '..', 'static', 'js', 'api.js');
const src = fs.readFileSync(apiPath, 'utf8');

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}
function section(title) { console.log('\n' + title); }

// ---------------------------------------------------------------- 假 DOM

function makeElement(id) {
  const classes = new Set();
  return {
    id: id,
    textContent: '',
    value: '',
    attrs: {},
    classList: {
      add: function () { for (const c of arguments) classes.add(c); },
      remove: function () { for (const c of arguments) classes.delete(c); },
      contains: function (c) { return classes.has(c); },
      toggle: function (c, force) {
        const on = force === undefined ? !classes.has(c) : !!force;
        if (on) { classes.add(c); } else { classes.delete(c); }
        return on;
      }
    },
    setAttribute: function (k, v) { this.attrs[k] = String(v); },
    getAttribute: function (k) { return this.attrs[k]; },
    hasClass: function (c) { return classes.has(c); }
  };
}

const IDS = [
  'currentWorkspaceName', 'toggleSidebarBtn',
  'ws-check-bank', 'ws-check-paper', 'ws-check-mistake',
  'ws-btn-bank', 'ws-btn-paper', 'ws-btn-mistake',
  'ws-tab-bank', 'ws-tab-paper', 'ws-tab-mistake'
];

const elements = {};
IDS.forEach(function (id) { elements[id] = makeElement(id); });
// 下拉菜单不存在 -> closeWorkspaceDropdown 会自己早退，正好覆盖那条分支
elements['workspaceDropdownMenu'] = null;

const sandbox = {
  console: console,
  window: {},
  document: {
    getElementById: function (id) {
      return Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null;
    },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    addEventListener: function () {},
    removeEventListener: function () {}
  }
};
sandbox.window = sandbox;
vm.createContext(sandbox);

// 抽取真实源码（不重写、不模拟）：selectWorkspace 末尾会关下拉，
// 关闭函数与它同作用域，一并取出来才是可运行的真实实现。
const selectMatch = src.match(/window\.selectWorkspace\s*=\s*function[\s\S]*?\n {8}\};/);
const closeMatch = src.match(/function closeWorkspaceDropdown\(\)\s*\{[\s\S]*?\n {8}\}/);
if (!selectMatch || !closeMatch) {
  console.log('找不到 window.selectWorkspace / closeWorkspaceDropdown 的实现，api.js 结构可能已变');
  process.exit(1);
}
vm.runInContext(closeMatch[0] + '\n' + selectMatch[0], sandbox,
  { filename: 'api.js#selectWorkspace' });

const selectWorkspace = sandbox.window.selectWorkspace;
if (typeof selectWorkspace !== 'function') {
  console.log('selectWorkspace 没有挂到 window 上');
  process.exit(1);
}

const WORKSPACES = ['bank', 'paper', 'mistake'];
const NAMES = { bank: '题库工作台', paper: '组卷工作台', mistake: '错题工作台' };

function tabState(wsId) {
  return {
    active: elements['ws-tab-' + wsId].hasClass('ws-tab-active'),
    aria: elements['ws-tab-' + wsId].getAttribute('aria-selected')
  };
}

// ---------------------------------------------------------------- 断言

function assertSwitched(wsId, label) {
  section('[切到 ' + NAMES[wsId] + ']');

  check('标题文本同步为「' + NAMES[wsId] + '」',
    elements['currentWorkspaceName'].textContent === NAMES[wsId],
    '实际: ' + JSON.stringify(elements['currentWorkspaceName'].textContent));

  WORKSPACES.forEach(function (other) {
    const expected = other === wsId;
    check('tab[' + other + '] 选中态 = ' + expected, tabState(other).active === expected,
      '实际 active=' + tabState(other).active);
    check('tab[' + other + '] aria-selected = ' + expected,
      tabState(other).aria === String(expected),
      '实际 aria-selected=' + JSON.stringify(tabState(other).aria));
    check('下拉勾选[' + other + '] 显示 = ' + expected,
      !elements['ws-check-' + other].hasClass('hidden') === expected,
      '实际 hidden=' + elements['ws-check-' + other].hasClass('hidden'));
    check('下拉项[' + other + '] 加粗 = ' + expected,
      elements['ws-btn-' + other].hasClass('font-medium') === expected,
      '实际 font-medium=' + elements['ws-btn-' + other].hasClass('font-medium'));
  });

  // 组卷台自己带画布，收起侧栏按钮在那边无意义
  check('侧栏收起按钮的显隐符合 ' + wsId,
    elements['toggleSidebarBtn'].hasClass('hidden') === (wsId === 'paper'),
    '实际 hidden=' + elements['toggleSidebarBtn'].hasClass('hidden'));
}

(function main() {
  console.log('api.js = ' + apiPath);
  console.log('（真实执行 api.js 的 selectWorkspace，不是正则猜源码）');

  section('[初态]');
  check('三套入口的元素都在',
    WORKSPACES.every(function (w) {
      return elements['ws-tab-' + w] && elements['ws-check-' + w] && elements['ws-btn-' + w];
    }));

  ['paper', 'mistake', 'bank'].forEach(function (wsId) {
    selectWorkspace(wsId, NAMES[wsId]);
    assertSwitched(wsId);
  });

  section('[结果]');
  console.log('  ' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail === 0 ? 0 : 1);
})();
