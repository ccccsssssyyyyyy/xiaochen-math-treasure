/**
 * 设置弹窗「备份与还原」tab 的接线校验 —— 跑 api.js 里真实的 switchSettingsTab。
 *
 * 为什么要有这个脚本：
 *   switchSettingsTab 用的是一份**硬编码白名单**（四个按钮 + 四个面板逐个 getElementById
 *   再逐个复位）。新增一个 tab 要改三处：取元素、进复位列表、加分支。漏掉任何一处都不会
 *   报错，只会表现为「点 tab 没反应」或「切走以后面板还留在屏幕上」。
 *
 *   静态断言只能证明「源码里写了这些字」，证明不了这些 id 真的存在于 index.html、也证明
 *   不了切过去以后另外四个面板真的被隐藏。所以这里做两件事：
 *     1. 把 index.html 里**真实存在的 id 集合**抽出来，凡是 api.js 去 getElementById 却
 *        在 HTML 里找不到的 id 一律记为 miss —— id 拼错当场暴露；
 *     2. 直接 eval api.js 里那段函数源码，驱动真实的切 tab 逻辑，断言可见性、高亮、
 *        保存按钮的显隐，以及加载函数有没有被触发。
 *
 * 用法: node tests/js/settings_tab_backup_check.js [api.js] [index.html]
 * 退出码: 0 全通过 / 1 有失败
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const apiPath = process.argv[2] || path.join(__dirname, '..', '..', 'static', 'js', 'api.js');
const htmlPath = process.argv[3] || path.join(__dirname, '..', '..', 'static', 'index.html');

const apiSrc = fs.readFileSync(apiPath, 'utf8');
const htmlSrc = fs.readFileSync(htmlPath, 'utf8');

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}
function section(title) { console.log('\n' + title); }

const TAB_NAMES = ['api', 'metadata', 'about', 'free', 'backup'];

// ---------------------------------------------------------------- 从 index.html 取真实 id

const presentIds = new Set();
for (const match of htmlSrc.matchAll(/\bid="([^"]+)"/g)) presentIds.add(match[1]);

/** settings-tab-* 面板在 HTML 里的初始 class（用来确认它们默认是隐藏的）。 */
function initialClasses(id) {
  const match = htmlSrc.match(new RegExp(`id="${id}"[^>]*class="([^"]*)"`));
  return match ? match[1].split(/\s+/).filter(Boolean) : [];
}

// ---------------------------------------------------------------- 假 DOM

function makeElement(id, classes) {
  const set = new Set(classes || []);
  return {
    id: id,
    classList: {
      add: function () { for (const c of arguments) set.add(c); },
      remove: function () { for (const c of arguments) set.delete(c); },
      contains: function (c) { return set.has(c); },
      toString: function () { return Array.from(set).join(' '); }
    }
  };
}

function visible(element) {
  return element ? !element.classList.contains('hidden') : false;
}

// ---------------------------------------------------------------- 抽出真实函数源码

const START = "window.switchSettingsTab = function";
const startIdx = apiSrc.indexOf(START);
if (startIdx === -1) {
  console.log('找不到 switchSettingsTab，api.js 结构可能已变');
  process.exit(1);
}
const END = "\n        };";
const endIdx = apiSrc.indexOf(END, startIdx);
const fnSource = apiSrc.slice(startIdx, endIdx + END.length);

// ---------------------------------------------------------------- 建沙箱并执行

const missing = [];
const elements = {};
function elementFor(id) {
  if (!presentIds.has(id)) {
    if (missing.indexOf(id) === -1) missing.push(id);
    return null;
  }
  if (!elements[id]) {
    elements[id] = makeElement(id, initialClasses(id));
  }
  return elements[id];
}

// 面板与按钮都按 HTML 里的初始 class 建好，这样「切走以后是否复原」才是有效断言
TAB_NAMES.forEach(function (name) {
  elementFor('btn-settings-' + name);
  elementFor('settings-tab-' + name);
});
elementFor('btnSettingsSave');

let loadPanelCalls = 0;
const sandbox = {
  console: console,
  document: {
    getElementById: elementFor,
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; }
  },
  fetch: function () { return Promise.resolve({ json: function () { return Promise.resolve({}); } }); },
  showToast: function () {},
  refreshAboutTabUpdateInfo: function () {},
  loadBackupPanel: function () { loadPanelCalls += 1; }
};
sandbox.window = sandbox;

vm.createContext(sandbox);
vm.runInContext(
  '(function () {\n' +
  '    let activeSettingsTab = "api";\n' +
  '    ' + fnSource + '\n' +
  '})();',
  sandbox,
  { filename: 'switchSettingsTab.js' }
);

// ---------------------------------------------------------------- 断言

(function main() {
  console.log('api.js = ' + apiPath);
  console.log('index.html = ' + htmlPath);

  section('[1] api.js 去查的 id 必须真的存在于 index.html');
  check('[1.1] 五个 tab 按钮与面板的 id 全部存在', missing.length === 0,
    'HTML 里找不到这些 id：' + missing.join(', '));
  check('[1.2] 新 tab 在 HTML 里是默认隐藏的',
    initialClasses('settings-tab-backup').indexOf('hidden') !== -1,
    'settings-tab-backup 的 class = ' + initialClasses('settings-tab-backup').join(' '));
  check('[1.3] 另外四个面板同样是默认隐藏的（除 api 外）',
    ['metadata', 'about', 'free'].every(function (name) {
      return initialClasses('settings-tab-' + name).indexOf('hidden') !== -1;
    }));

  section('[2] 切到「备份与还原」：面板出现、其余让位');

  sandbox.switchSettingsTab('backup');

  check('[2.1] backup 面板显示出来', visible(elements['settings-tab-backup']));
  check('[2.2] api 面板被隐藏', !visible(elements['settings-tab-api']));
  check('[2.3] metadata 面板被隐藏', !visible(elements['settings-tab-metadata']));
  check('[2.4] about 面板被隐藏', !visible(elements['settings-tab-about']));
  check('[2.5] free 面板被隐藏', !visible(elements['settings-tab-free']));
  check('[2.6] 同一时刻只有一个面板可见',
    TAB_NAMES.filter(function (n) { return visible(elements['settings-tab-' + n]); }).length === 1,
    TAB_NAMES.filter(function (n) { return visible(elements['settings-tab-' + n]); }).join(', '));

  const backupBtn = elements['btn-settings-backup'];
  check('[2.7] backup 按钮被加上高亮',
    backupBtn.classList.contains('border-brand-500') && backupBtn.classList.contains('text-brand-600'));
  check('[2.8] backup 按钮去掉了暗淡样式',
    !backupBtn.classList.contains('border-transparent'));

  section('[3] 其余按钮的高亮必须被撤销（漏进复位列表就会同时亮两个）');
  ['api', 'metadata', 'about', 'free'].forEach(function (name) {
    const btn = elements['btn-settings-' + name];
    check('[3.' + (TAB_NAMES.indexOf(name) + 1) + '] ' + name + ' 按钮不再是高亮态',
      !btn.classList.contains('border-brand-500') && !btn.classList.contains('text-brand-600'));
  });
  check('[3.5] 只有 backup 按钮是高亮的',
    TAB_NAMES.filter(function (n) {
      return elements['btn-settings-' + n].classList.contains('border-brand-500');
    }).join(',') === 'backup',
    TAB_NAMES.filter(function (n) {
      return elements['btn-settings-' + n].classList.contains('border-brand-500');
    }).join(', '));

  section('[4] 这个 tab 没有可保存的配置项，[保存配置] 必须收起');
  check('[4.1] btnSettingsSave 被隐藏', elements['btnSettingsSave'].classList.contains('hidden'));
  check('[4.2] 切到该 tab 会触发面板加载', loadPanelCalls === 1, '调用次数 ' + loadPanelCalls);

  section('[5] 切回「API 设置」：一切复原，面板不能留在屏幕上');

  sandbox.switchSettingsTab('api');

  check('[5.1] api 面板回到可见', visible(elements['settings-tab-api']));
  check('[5.2] backup 面板被收起', !visible(elements['settings-tab-backup']));
  check('[5.3] [保存配置] 回来了', !elements['btnSettingsSave'].classList.contains('hidden'));
  check('[5.4] api 按钮重新高亮', elements['btn-settings-api'].classList.contains('border-brand-500'));
  check('[5.5] backup 按钮的高亮被撤销',
    !elements['btn-settings-backup'].classList.contains('border-brand-500'));
  check('[5.6] 再次切到 backup 会再触发一次加载', (function () {
    sandbox.switchSettingsTab('backup');
    return loadPanelCalls === 2;
  })(), '调用次数 ' + loadPanelCalls);

  section('[6] 每个 tab 都切一遍，确认没有互相打架');

  let allOk = true;
  const detail = [];
  TAB_NAMES.forEach(function (name) {
    sandbox.switchSettingsTab(name);
    const visibleNames = TAB_NAMES.filter(function (n) { return visible(elements['settings-tab-' + n]); });
    const highlighted = TAB_NAMES.filter(function (n) {
      return elements['btn-settings-' + n].classList.contains('border-brand-500');
    });
    if (visibleNames.length !== 1 || visibleNames[0] !== name) {
      allOk = false;
      detail.push(name + ' 的面板可见性异常：' + visibleNames.join(', '));
    }
    if (highlighted.length !== 1 || highlighted[0] !== name) {
      allOk = false;
      detail.push(name + ' 的按钮高亮异常：' + highlighted.join(', '));
    }
  });
  check('[6.1] 五个 tab 逐个切换后，可见面板与高亮按钮都唯一且对应', allOk, detail.join('; '));

  section('[7] 保存按钮的显隐规则与「这个 tab 有没有配置可存」一致');
  const savedTabs = [];
  TAB_NAMES.forEach(function (name) {
    sandbox.switchSettingsTab(name);
    if (!elements['btnSettingsSave'].classList.contains('hidden')) savedTabs.push(name);
  });
  // about 也隐藏保存按钮（它只读展示版本与环境信息），所以这里只有 api / metadata / free 三个
  check('[7.1] 只有 api / metadata / free 三个 tab 显示[保存配置]',
    savedTabs.join(',') === 'api,metadata,free', savedTabs.join(', '));
  check('[7.2] 新增的 backup tab 与 about 一样收起保存按钮',
    savedTabs.indexOf('backup') === -1 && savedTabs.indexOf('about') === -1);

  console.log('\n────────────────────────────────────────');
  console.log(fail === 0 ? '全部通过：' + pass + ' 项' : '失败 ' + fail + ' 项 / 共 ' + (pass + fail) + ' 项');
  process.exit(fail === 0 ? 0 : 1);
})();
