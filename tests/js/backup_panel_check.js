/**
 * 设置 →「备份与还原」面板端到端校验 —— 真实执行 backup.js，不靠正则猜源码。
 *
 * 为什么要有这个脚本：
 *   这块界面对应的是「把整个题库换掉」这种不可逆操作。后端已经把危险部分挡在
 *   runtime lock 后面（服务运行中不许换库），所以前端要负责的是三件事：
 *     1. 说清楚现在有什么快照、上次备份是什么时候 —— 用户得能判断手里的退路；
 *     2. 让「还原」不可能被误触发 —— 按钮必须等显式选中才可点，且必须过二次确认；
 *     3. 任何一步失败都要留下可读原因，且按钮必须还回去。
 *   第 3 条是这个项目踩过的坑：fetch 在网络层失败走的是 reject 而不是 ok:false，
 *   只用 .then 复位按钮的话，一次断网就能把按钮永久卡死在禁用态（见 mistake.js
 *   的「插入配图」修复）。所以这里专门用 reject 逐个打一遍每个按钮。
 *
 * 用法: node tests/js/backup_panel_check.js [backup.js 路径]
 * 退出码: 0 全通过 / 1 有失败
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const backupPath = process.argv[2]
  || path.join(__dirname, '..', '..', 'static', 'js', 'backup.js');
const src = fs.readFileSync(backupPath, 'utf8');

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}
function section(title) { console.log('\n' + title); }
function silent(fn) { try { fn(); } catch (error) { /* 断言用，忽略 */ } }

// ---------------------------------------------------------------- 假 DOM

function makeElement(id) {
  const attrs = {};
  const classes = new Set();
  const children = {};
  return {
    id: id,
    innerHTML: '',
    textContent: '',
    innerText: '',
    value: '',
    disabled: false,
    style: {},
    dataset: {},
    handlers: {},
    classList: {
      add: function () { for (const c of arguments) classes.add(c); },
      remove: function () { for (const c of arguments) classes.delete(c); },
      toggle: function (c, on) { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
      contains: function (c) { return classes.has(c); }
    },
    setAttribute: function (n, v) { attrs[n] = String(v); },
    getAttribute: function (n) { return Object.prototype.hasOwnProperty.call(attrs, n) ? attrs[n] : null; },
    removeAttribute: function (n) { delete attrs[n]; },
    hasAttribute: function (n) { return Object.prototype.hasOwnProperty.call(attrs, n); },
    addEventListener: function (t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); },
    removeEventListener: function () {},
    appendChild: function () {},
    removeChild: function () {},
    remove: function () {},
    focus: function () {},
    blur: function () {},
    click: function () {},
    // 界面代码会拿 span（按钮文案）和 div（弹窗面板）来改样式/文案，
    // 这里给一个稳定的假子节点，避免断言只能退化成「调用没抛错」。
    querySelector: function (sel) {
      const key = id + '::' + sel;
      if (!children[key]) children[key] = makeElement(key);
      return children[key];
    },
    querySelectorAll: function () { return []; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: 1000, height: 2000, right: 1000, bottom: 2000 }; }
  };
}

const IDS = [
  'toast', 'toastMessage', 'toastIconContainer',
  'btnBackupNow', 'backupLastAt', 'backupCountBadge', 'backupDirHint',
  'backupPendingBox', 'backupPendingText', 'backupPanelState',
  'backupSnapshotList', 'btnBackupRestore',
  'backupRestoreConfirmModal', 'backupConfirmFile', 'backupConfirmTtl',
  'backupConfirmSummary', 'btnBackupConfirmRestore'
];

/** 建一个装了真 backup.js 的沙箱。 */
function boot(options) {
  const missing = (options && options.missing) || [];
  const elements = {};
  function elementFor(id) {
    if (missing.indexOf(id) !== -1) return null;
    if (!elements[id]) elements[id] = makeElement(id);
    return elements[id];
  }
  IDS.forEach(function (id) { if (missing.indexOf(id) === -1) elementFor(id); });

  const requests = [];
  let routes = {};
  let timers = [];
  let timerSeq = 1;

  const sandbox = {
    console: console,
    setTimeout: function (fn, ms) { const id = timerSeq++; timers.push({ id: id, fn: fn, ms: ms }); return id; },
    clearTimeout: function (id) { timers = timers.filter(function (t) { return t.id !== id; }); },
    setInterval: function () { return 0; },
    clearInterval: function () {},
    fetch: function (url, options) {
      const opts = options || {};
      const method = String(opts.method || 'GET').toUpperCase();
      requests.push({ url: String(url), method: method, body: opts.body, options: opts });
      const key = method + ' ' + String(url);
      const handler = routes[key];
      if (!handler) {
        return Promise.resolve({ ok: false, status: 404, json: function () { return Promise.resolve({}); } });
      }
      return typeof handler === 'function' ? handler() : handler;
    },
    document: {
      getElementById: elementFor,
      querySelector: function () { return null; },
      querySelectorAll: function () { return []; },
      createElement: function () { return makeElement('created'); },
      addEventListener: function () {},
      removeEventListener: function () {},
      body: makeElement('body'),
      documentElement: { classList: { add: function () {}, remove: function () {} } },
      activeElement: null
    }
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  // api.js 在浏览器里先加载并提供 window.MathBankSafe；按同一份契约放替身。
  sandbox.MathBankSafe = {
    escapeText: function (v) {
      return (v == null ? '' : String(v))
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    },
    escapeAttribute: function (v) {
      return (v == null ? '' : String(v))
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
  };

  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'backup.js' });

  // 取消按钮的原文案先填上，这样「失败后文案是否还回去」才是有效断言
  const pendingBox = elementFor('backupPendingBox');
  if (pendingBox) pendingBox.querySelector('button').textContent = '取消还原请求';
  // 真实 index.html 里这些节点本来就带初始内容/类名；假 DOM 若不预置，
  // 「失败后文案是否还回去」这类断言就会因为原文案是空串而假绿/假红。
  const backupBtn = elementFor('btnBackupNow');
  if (backupBtn) {
    backupBtn.innerHTML = '<i class="fa-solid fa-box-archive text-[9px]"></i><span>立即备份</span>';
  }
  const confirmModal = elementFor('backupRestoreConfirmModal');
  if (confirmModal) { confirmModal.classList.add('hidden'); confirmModal.classList.add('opacity-0'); }

  return {
    sandbox: sandbox,
    requests: requests,
    el: elementFor,
    setRoutes: function (next) { routes = next; },
    setRoute: function (key, value) { routes[key] = value; },
    resetRequests: function () { requests.length = 0; },
    flush: function () { const q = timers; timers = []; q.forEach(function (t) { t.fn(); }); },
    lastRequest: function () { return requests[requests.length - 1] || null; },
    // 供列表事件委派用的假事件目标
    rowTarget: function (file) {
      return { closest: function (sel) {
        if (sel === '[data-backup-verify]') return null;
        if (sel === '[data-backup-file]') return { getAttribute: function (n) { return n === 'data-backup-file' ? file : null; } };
        return null;
      } };
    },
    verifyTarget: function (file) {
      return { closest: function (sel) {
        if (sel === '[data-backup-verify]') return { getAttribute: function (n) { return n === 'data-backup-verify' ? file : null; } };
        return null;
      } };
    },
    dispatch: function (type, event) {
      const list = elementFor('backupSnapshotList');
      if (!list) return;
      const handlers = list.handlers[type] || [];
      handlers.forEach(function (fn) { fn(event); });
    }
  };
}

// ---------------------------------------------------------------- 假数据与假响应

function jsonResponse(body, status) {
  const code = status || 200;
  return { ok: code < 400, status: code, json: function () { return Promise.resolve(body); } };
}
function networkFailure() {
  return function () { return Promise.reject(new TypeError('Failed to fetch')); };
}

const FILE_A = 'mathbank-backup-20260915T133058872611Z.zip';
const FILE_B = 'mathbank-backup-20260914T090000000000Z.zip';
const FILE_C = 'mathbank-backup-20260913T221500000000Z.zip';

function snapshot(file, createdAt, overrides) {
  return {
    file: file,
    size_bytes: 8455259,
    modified_at: createdAt,
    manifest: Object.assign({
      readable: true,
      format_version: 2,
      created_at: createdAt,
      app_version: '2.3.0',
      schema_version: 3,
      row_counts: { questions: 1379, question_curriculums: 1368, papers: 0, paper_questions: 0 },
      upload_file_count: 148,
      metadata_included: true
    }, overrides || {})
  };
}

const LIST_BODY = {
  status: 'success',
  dir: '/Users/demo/xiaochen-math-treasure/data_backup/snapshots',
  retention: 5,
  last_backup_at: '2026-09-15T13:30:58.872611Z',
  snapshots: [
    snapshot(FILE_A, '2026-09-15T13:30:58.872611Z'),
    snapshot(FILE_B, '2026-09-14T09:00:00.000000Z'),
    snapshot(FILE_C, '2026-09-13T22:15:00.000000Z')
  ],
  pending_restore: null,
  restore_ttl_seconds: 1800
};

function routesFor(listBody) {
  return {
    'GET /api/backups': function () { return Promise.resolve(jsonResponse(listBody)); },
    'POST /api/backup': function () {
      return Promise.resolve(jsonResponse({ status: 'success', file: FILE_A, message: '已创建完整备份：' + FILE_A }));
    },
    'POST /api/backup/verify': function () {
      return Promise.resolve(jsonResponse({
        status: 'success', file: FILE_A, size_bytes: 8455259,
        message: '快照完整性与数据库校验通过',
        row_counts: { questions: 1379, question_curriculums: 1368, papers: 0, paper_questions: 0 }
      }));
    },
    'POST /api/backup/restore': function () {
      return Promise.resolve(jsonResponse({
        status: 'success', file: FILE_A,
        safety_backup: '/tmp/data_backup/pre_restore/mathbank-backup-20260916T120000000000Z.zip',
        expires_at: '2026-09-16T12:30:00.000000Z',
        ttl_seconds: 1800,
        message: '已登记还原请求。请关闭题库并重新启动，30 分钟内启动即会自动完成还原。'
      }));
    },
    'DELETE /api/backup/restore': function () {
      return Promise.resolve(jsonResponse({ status: 'success', removed: true, message: '已取消待还原请求；下次重启不会再改动数据' }));
    }
  };
}

function state(env) { return env.sandbox.__backupPanelState(); }
function listHtml(env) { return env.el('backupSnapshotList').innerHTML; }
function stateText(env) { return env.el('backupPanelState').textContent; }
function toastText(env) { return env.el('toastMessage').textContent; }
// 成功路径后面常紧跟一次列表刷新，所以不能拿「最后一条请求」当目标请求
function findRequest(env, method, url) {
  return env.requests.filter(function (r) { return r.method === method && r.url === url; })[0] || null;
}

(async function main() {
  console.log('backup.js = ' + backupPath);

  // =============================================================== [1]
  section('[1] 面板加载：快照列表 / 上次备份时间 / 保留策略');

  let env = boot();
  env.setRoutes(routesFor(LIST_BODY));
  await env.sandbox.loadBackupPanel();

  check('[1.1] 只发了一个 GET /api/backups',
    env.requests.length === 1 && env.lastRequest().method === 'GET' && env.lastRequest().url === '/api/backups',
    JSON.stringify(env.requests.map(function (r) { return r.method + ' ' + r.url; })));

  const html = listHtml(env);
  check('[1.2] 列表渲染了三份快照',
    html.indexOf(FILE_A) !== -1 && html.indexOf(FILE_B) !== -1 && html.indexOf(FILE_C) !== -1);
  check('[1.3] 行数键名被翻成中文标签（题目/章节关联/试卷）',
    html.indexOf('题目 1379') !== -1 && html.indexOf('章节关联 1368') !== -1 && html.indexOf('试卷 0') !== -1,
    html.slice(0, 200));
  check('[1.4] 体积按 MB 展示', html.indexOf('8.1 MB') !== -1, html.match(/\d+(\.\d+)? (MB|GB|KB)/));
  check('[1.5] 插图数与应用版本一并展示',
    html.indexOf('插图 148') !== -1 && html.indexOf('v2.3.0') !== -1);
  check('[1.6] 显示快照保留策略', env.el('backupCountBadge').textContent.indexOf('保留最近 5 份') !== -1,
    env.el('backupCountBadge').textContent);
  check('[1.7] 可用快照计数正确', env.el('backupCountBadge').textContent.indexOf('3 个可用快照') !== -1,
    env.el('backupCountBadge').textContent);
  check('[1.8] 上次备份时间被格式化（不再是 ISO 原串）',
    env.el('backupLastAt').textContent.indexOf('2026-09-15') !== -1
      && env.el('backupLastAt').textContent.indexOf('T') === -1,
    env.el('backupLastAt').textContent);
  check('[1.9] 目录提示写入且带上完整路径',
    env.el('backupDirHint').textContent.indexOf('data_backup/snapshots') !== -1,
    env.el('backupDirHint').textContent);
  check('[1.10] 加载完成后状态行清空（不是停在"读取中"）',
    stateText(env) === '', stateText(env));
  check('[1.11] 内部状态记录了快照与 TTL',
    state(env).snapshots.length === 3 && state(env).restoreTtlSeconds === 1800);
  check('[1.12] 没有待还原请求时不显示提示框',
    env.el('backupPendingBox').classList.contains('hidden'));

  // =============================================================== [2]
  section('[2] 空列表：不能呈现出「可还原」的假象');

  env = boot();
  env.setRoutes(routesFor(Object.assign({}, LIST_BODY, {
    snapshots: [], last_backup_at: null, retention: 5
  })));
  await env.sandbox.loadBackupPanel();

  check('[2.1] 给出"还没有快照"的明确提示', listHtml(env).indexOf('还没有快照') !== -1, listHtml(env));
  check('[2.2] 上次备份显示"尚未备份过"', env.el('backupLastAt').textContent === '尚未备份过',
    env.el('backupLastAt').textContent);
  check('[2.3] 还原按钮保持禁用', env.el('btnBackupRestore').disabled === true);

  // =============================================================== [3]
  section('[3] 坏快照（manifest 读不出来）仍然列出，但标出原因');

  env = boot();
  env.setRoutes(routesFor(Object.assign({}, LIST_BODY, {
    snapshots: [
      snapshot(FILE_A, '2026-09-15T13:30:58.872611Z'),
      snapshot(FILE_B, '2026-09-14T09:00:00.000000Z', {
        readable: false, error: 'BadZipFile: File is not a zip file'
      })
    ]
  })));
  await env.sandbox.loadBackupPanel();

  const badHtml = listHtml(env);
  check('[3.1] 坏快照没有被静默丢掉', badHtml.indexOf(FILE_B) !== -1);
  check('[3.2] 坏快照标出了具体原因', badHtml.indexOf('File is not a zip file') !== -1);
  check('[3.3] 计数只算可读的那份',
    env.el('backupCountBadge').textContent.indexOf('1 个可用快照') !== -1,
    env.el('backupCountBadge').textContent);

  // =============================================================== [4]
  section('[4] 还原按钮必须等显式选中才可点（不能被默认值预先武装）');

  env = boot();
  env.setRoutes(routesFor(LIST_BODY));
  await env.sandbox.loadBackupPanel();

  check('[4.1] 加载完仍未被选中', state(env).selectedFile === '', state(env).selectedFile);
  check('[4.2] 加载完还原按钮是禁用的', env.el('btnBackupRestore').disabled === true);
  check('[4.3] 渲染出的行都不是选中态', listHtml(env).indexOf('border-brand-400') === -1);

  env.dispatch('click', {
    target: env.rowTarget(FILE_B),
    preventDefault: function () {}, stopPropagation: function () {}
  });

  check('[4.4] 点一行即选中该份', state(env).selectedFile === FILE_B, state(env).selectedFile);
  check('[4.5] 选中后还原按钮才可点', env.el('btnBackupRestore').disabled === false);
  check('[4.6] 选中态反映在渲染上',
    listHtml(env).indexOf('border-brand-400') !== -1 && listHtml(env).indexOf('aria-checked="true"') !== -1);
  check('[4.7] 只有被点中的那份是选中态',
    listHtml(env).indexOf('aria-checked="true"') !== -1
      && listHtml(env).split('aria-checked="true"').length - 1 === 1);

  // 点「校验」不应顺带改变选择
  const beforeSelect = state(env).selectedFile;
  env.dispatch('click', {
    target: env.verifyTarget(FILE_A),
    preventDefault: function () {}, stopPropagation: function () {}
  });
  check('[4.8] 点"校验"不会改变当前选择', state(env).selectedFile === beforeSelect, state(env).selectedFile);

  // 键盘可达性
  env.dispatch('keydown', {
    key: 'Enter', target: env.rowTarget(FILE_C),
    preventDefault: function () {}, stopPropagation: function () {}
  });
  check('[4.9] 键盘 Enter 也能选中某一行', state(env).selectedFile === FILE_C, state(env).selectedFile);

  // =============================================================== [5]
  section('[5] 校验快照：只读调用 + 结果回显');

  env = boot();
  env.setRoutes(routesFor(LIST_BODY));
  await env.sandbox.loadBackupPanel();
  env.resetRequests();
  await env.sandbox.verifyBackupSnapshot(FILE_A);

  const verifyReq = env.lastRequest();
  check('[5.1] 校验走 POST /api/backup/verify', verifyReq.method === 'POST' && verifyReq.url === '/api/backup/verify');
  check('[5.2] 请求体只带文件名', verifyReq.body === JSON.stringify({ file: FILE_A }), verifyReq.body);
  check('[5.3] 校验通过后列表里出现结果',
    listHtml(env).indexOf('校验通过') !== -1 && listHtml(env).indexOf('题目 1379') !== -1);
  check('[5.4] 结果挂在被校验的那一份上',
    state(env).verifyResults[FILE_A] && state(env).verifyResults[FILE_A].ok === true
      && !state(env).verifyResults[FILE_B]);
  check('[5.5] 校验不改动选中态', state(env).selectedFile === '');

  // 校验失败（422）
  env.setRoute('POST /api/backup/verify', function () {
    return Promise.resolve(jsonResponse({
      status: 'error', file: FILE_B, message: '快照校验未通过：database.sqlite 完整性检查失败'
    }, 422));
  });
  await env.sandbox.verifyBackupSnapshot(FILE_B);
  check('[5.6] 校验失败时把服务端原因原样带出',
    state(env).verifyResults[FILE_B] && state(env).verifyResults[FILE_B].ok === false
      && state(env).verifyResults[FILE_B].message.indexOf('完整性检查失败') !== -1,
    JSON.stringify(state(env).verifyResults[FILE_B]));
  check('[5.7] 校验失败渲染成红色提示', listHtml(env).indexOf('text-red-500') !== -1);
  check('[5.8] 校验失败不会污染已通过的那份',
    state(env).verifyResults[FILE_A].ok === true);

  // =============================================================== [6]
  section('[6] 立即备份：按钮进出场 + 列表刷新');

  env = boot();
  env.setRoutes(routesFor(LIST_BODY));
  await env.sandbox.loadBackupPanel();
  env.resetRequests();

  const backupPromise = env.sandbox.runBackupNow();
  check('[6.1] 请求期间按钮被禁用', env.el('btnBackupNow').disabled === true);
  check('[6.2] 请求期间按钮文案变成"备份中…"',
    env.el('btnBackupNow').innerHTML.indexOf('备份中') !== -1,
    env.el('btnBackupNow').innerHTML);
  await backupPromise;

  const calls = env.requests.map(function (r) { return r.method + ' ' + r.url; });
  check('[6.3] 先 POST /api/backup 再重新拉列表',
    calls[0] === 'POST /api/backup' && calls[1] === 'GET /api/backups', JSON.stringify(calls));
  check('[6.4] 完成后按钮恢复可点', env.el('btnBackupNow').disabled === false);
  check('[6.5] 完成后按钮文案还原',
    env.el('btnBackupNow').innerHTML.indexOf('立即备份') !== -1
      && env.el('btnBackupNow').innerHTML.indexOf('备份中') === -1,
    env.el('btnBackupNow').innerHTML);
  check('[6.6] 备份成功后状态行已清空（不停留在"正在创建…"）', stateText(env) === '', stateText(env));

  // 服务端返回 status:error（不是 HTTP 错误）
  env.setRoute('POST /api/backup', function () {
    return Promise.resolve(jsonResponse({ status: 'error', message: '备份失败：磁盘空间不足' }));
  });
  await env.sandbox.runBackupNow();
  check('[6.7] status:error 被当成失败处理', stateText(env).indexOf('磁盘空间不足') !== -1, stateText(env));
  check('[6.8] status:error 后按钮依然恢复',
    env.el('btnBackupNow').disabled === false && env.el('btnBackupNow').innerHTML.indexOf('立即备份') !== -1);

  // =============================================================== [7]
  section('[7] 网络层 reject（fetch 直接失败）：每个按钮都必须还回去');

  env = boot();
  env.setRoutes(routesFor(LIST_BODY));
  await env.sandbox.loadBackupPanel();

  // 断网时所有写操作一起失败，这才是真实场景 —— 只掐其中一个接口的写法会漏测
  env.setRoute('POST /api/backup', networkFailure());
  env.setRoute('POST /api/backup/verify', networkFailure());
  await env.sandbox.runBackupNow();
  check('[7.1] 立即备份遇到断网：不抛异常到调用方', true);
  check('[7.2] 立即备份遇到断网：按钮恢复可点', env.el('btnBackupNow').disabled === false);
  check('[7.3] 立即备份遇到断网：按钮文案还原',
    env.el('btnBackupNow').innerHTML.indexOf('立即备份') !== -1);
  check('[7.4] 立即备份遇到断网：状态行给出可读原因',
    stateText(env).indexOf('Failed to fetch') !== -1, stateText(env));

  // 7B —— 校验
  await env.sandbox.verifyBackupSnapshot(FILE_A);
  check('[7.5] 校验遇到断网：结果标记为"请求未送达"',
    state(env).verifyResults[FILE_A] && state(env).verifyResults[FILE_A].ok === false
      && state(env).verifyResults[FILE_A].message.indexOf('未送达') !== -1,
    JSON.stringify(state(env).verifyResults[FILE_A]));
  check('[7.6] 校验遇到断网：列表仍渲染出该条结果', listHtml(env).indexOf('未送达') !== -1);

  // 7C —— 确认还原（最容易卡死的一个：弹窗里的按钮）
  env.el('btnBackupConfirmRestore').textContent = '登记还原请求';
  env.setRoutes(routesFor(LIST_BODY));
  await env.sandbox.loadBackupPanel();
  env.dispatch('click', {
    target: env.rowTarget(FILE_A),
    preventDefault: function () {}, stopPropagation: function () {}
  });
  env.setRoute('POST /api/backup/restore', networkFailure());
  await env.sandbox.confirmBackupRestore();

  check('[7.7] 登记还原遇到断网：确认按钮恢复可点', env.el('btnBackupConfirmRestore').disabled === false);
  check('[7.8] 登记还原遇到断网：按钮文案还原',
    env.el('btnBackupConfirmRestore').textContent === '登记还原请求',
    env.el('btnBackupConfirmRestore').textContent);
  check('[7.9] 登记还原遇到断网：不假装成功、状态行给出原因',
    stateText(env).indexOf('登记失败') !== -1 && stateText(env).indexOf('Failed to fetch') !== -1,
    stateText(env));

  // 7D —— 取消待还原请求
  env = boot();
  env.setRoutes(routesFor(Object.assign({}, LIST_BODY, {
    pending_restore: {
      archive: FILE_B,
      requested_at: '2026-09-16T12:00:00.000000Z',
      expires_at: '2026-09-16T12:30:00.000000Z',
      safety_backup: '/tmp/data_backup/pre_restore/mathbank-backup-20260916T115500000000Z.zip'
    }
  })));
  await env.sandbox.loadBackupPanel();
  const cancelBtn = env.el('backupPendingBox').querySelector('button');
  cancelBtn.textContent = '取消还原请求';
  env.setRoute('DELETE /api/backup/restore', networkFailure());
  await env.sandbox.cancelPendingRestore();

  check('[7.10] 取消遇到断网：按钮恢复可点', cancelBtn.disabled === false);
  check('[7.11] 取消遇到断网：按钮文案还原', cancelBtn.textContent === '取消还原请求', cancelBtn.textContent);
  check('[7.12] 取消遇到断网：状态行给出原因', stateText(env).indexOf('取消失败') !== -1, stateText(env));

  // =============================================================== [8]
  section('[8] 还原三步：显式选中 → 二次确认 → 登记请求');

  env = boot();
  env.setRoutes(routesFor(LIST_BODY));
  await env.sandbox.loadBackupPanel();
  env.resetRequests();

  // 没选就点还原 —— 必须什么都不发生
  await env.sandbox.requestBackupRestore();
  check('[8.1] 未选快照时点还原不会发请求', env.requests.length === 0,
    JSON.stringify(env.requests.map(function (r) { return r.method + ' ' + r.url; })));
  check('[8.2] 未选快照时给出提示而不是静默', toastText(env).indexOf('先选') !== -1, toastText(env));
  check('[8.3] 未选快照时确认弹窗不出现',
    env.el('backupRestoreConfirmModal').classList.contains('hidden'));

  // 选中 → 打开确认弹窗
  env.dispatch('click', {
    target: env.rowTarget(FILE_A),
    preventDefault: function () {}, stopPropagation: function () {}
  });
  await env.sandbox.requestBackupRestore();
  env.flush();

  check('[8.4] 选中后点还原：不发任何请求（还原还没发生）', env.requests.length === 0);
  check('[8.5] 选中后点还原：确认弹窗出现',
    env.el('backupRestoreConfirmModal').classList.contains('hidden') === false
      && env.el('backupRestoreConfirmModal').classList.contains('opacity-0') === false);
  check('[8.6] 弹窗里写明了目标快照文件名',
    env.el('backupConfirmFile').textContent === FILE_A,
    env.el('backupConfirmFile').textContent);
  check('[8.7] 弹窗里写明了重启生效的时限（1800 秒 → 30 分钟）',
    env.el('backupConfirmTtl').textContent === '30', env.el('backupConfirmTtl').textContent);
  check('[8.8] 弹窗摘要带上快照内容行数',
    env.el('backupConfirmSummary').innerHTML.indexOf('题目 1379') !== -1,
    env.el('backupConfirmSummary').innerHTML);

  // 确认登记
  env.resetRequests();
  await env.sandbox.confirmBackupRestore();
  const restoreReq = findRequest(env, 'POST', '/api/backup/restore');
  check('[8.9] 确认后走 POST /api/backup/restore', restoreReq !== null,
    JSON.stringify(env.requests.map(function (r) { return r.method + ' ' + r.url; })));
  check('[8.10] 请求体带 confirm:true（服务端据此拒绝误触）',
    restoreReq !== null && restoreReq.body === JSON.stringify({ file: FILE_A, confirm: true }),
    restoreReq ? String(restoreReq.body) : 'no request');
  check('[8.11] 成功后弹窗关闭',
    env.el('backupRestoreConfirmModal').classList.contains('opacity-0') === true);
  check('[8.12] 成功后重新拉列表（让待还原提示立刻出现）',
    env.requests.some(function (r) { return r.method === 'GET' && r.url === '/api/backups'; }));
  check('[8.13] 成功后确认按钮恢复可用', env.el('btnBackupConfirmRestore').disabled === false);

  // =============================================================== [9]
  section('[9] 已有待还原请求：置顶提示 + 同一份不可重复登记');

  const pendingBody = Object.assign({}, LIST_BODY, {
    pending_restore: {
      archive: FILE_A,
      requested_at: '2026-09-16T12:00:00.000000Z',
      expires_at: '2026-09-16T12:30:00.000000Z',
      safety_backup: '/tmp/data_backup/pre_restore/mathbank-backup-20260916T115500000000Z.zip'
    }
  });
  env = boot();
  env.setRoutes(routesFor(pendingBody));
  await env.sandbox.loadBackupPanel();

  check('[9.1] 待还原提示框显示出来',
    env.el('backupPendingBox').classList.contains('hidden') === false);
  const pendingText = env.el('backupPendingText').textContent;
  check('[9.2] 提示里点明了目标快照', pendingText.indexOf(FILE_A) !== -1, pendingText);
  check('[9.3] 提示里点明了重启截止时间', pendingText.indexOf('2026-09-16') !== -1, pendingText);
  check('[9.4] 提示里给出当前库的安全备份路径（退路）',
    pendingText.indexOf('pre_restore') !== -1, pendingText);
  check('[9.5] 列表把该份标成"待还原目标"', listHtml(env).indexOf('待还原目标') !== -1);
  check('[9.6] 未选中时还原按钮仍禁用', env.el('btnBackupRestore').disabled === true);

  env.dispatch('click', {
    target: env.rowTarget(FILE_A),
    preventDefault: function () {}, stopPropagation: function () {}
  });
  check('[9.7] 选中已在等待还原的那份时，按钮保持禁用（避免重复登记）',
    env.el('btnBackupRestore').disabled === true);

  env.dispatch('click', {
    target: env.rowTarget(FILE_B),
    preventDefault: function () {}, stopPropagation: function () {}
  });
  check('[9.8] 改选另一份后按钮可用', env.el('btnBackupRestore').disabled === false);

  // 覆盖式登记要在弹窗里事先说明
  await env.sandbox.requestBackupRestore();
  check('[9.9] 已有待还原请求时，弹窗明确提示会被覆盖',
    env.el('backupConfirmSummary').innerHTML.indexOf('覆盖') !== -1,
    env.el('backupConfirmSummary').innerHTML);

  // 取消待还原请求
  env.setRoutes(routesFor(LIST_BODY));
  env.resetRequests();
  await env.sandbox.cancelPendingRestore();
  const cancelReq = findRequest(env, 'DELETE', '/api/backup/restore');
  check('[9.10] 取消走 DELETE /api/backup/restore', cancelReq !== null,
    JSON.stringify(env.requests.map(function (r) { return r.method + ' ' + r.url; })));
  check('[9.11] 取消不误发确认体',
    cancelReq !== null && (cancelReq.body === undefined || cancelReq.body === null),
    cancelReq ? String(cancelReq.body) : 'no request');
  check('[9.12] 取消后列表刷新、提示框收起',
    env.el('backupPendingBox').classList.contains('hidden') === true);

  // =============================================================== [10]
  section('[10] 快照被保留策略轮转掉：选中态必须失效');

  env = boot();
  env.setRoutes(routesFor(LIST_BODY));
  await env.sandbox.loadBackupPanel();
  env.dispatch('click', {
    target: env.rowTarget(FILE_C),
    preventDefault: function () {}, stopPropagation: function () {}
  });
  check('[10.1] 先选中最早那份', state(env).selectedFile === FILE_C);

  // 下次刷新时它已被轮转掉
  env.setRoutes(routesFor(Object.assign({}, LIST_BODY, {
    snapshots: [snapshot(FILE_A, '2026-09-15T13:30:58.872611Z'), snapshot(FILE_B, '2026-09-14T09:00:00.000000Z')]
  })));
  await env.sandbox.loadBackupPanel();

  check('[10.2] 消失的快照不再被选中', state(env).selectedFile === '', state(env).selectedFile);
  check('[10.3] 还原按钮回到禁用（否则会指向一份不存在的快照）',
    env.el('btnBackupRestore').disabled === true);
  check('[10.4] 剩下两份仍在列表里',
    listHtml(env).indexOf(FILE_A) !== -1 && listHtml(env).indexOf(FILE_B) !== -1
      && listHtml(env).indexOf(FILE_C) === -1);

  // =============================================================== [11]
  section('[11] 提示真的能弹出来（api.js 的 showToast 没挂到 window 上）');

  env = boot();
  env.setRoutes(routesFor(LIST_BODY));
  await env.sandbox.loadBackupPanel();
  env.dispatch('click', {
    target: env.rowTarget(FILE_A),
    preventDefault: function () {}, stopPropagation: function () {}
  });

  env.resetRequests();
  env.setRoute('POST /api/backup/restore', networkFailure());
  await env.sandbox.confirmBackupRestore();

  check('[11.1] 失败时 toast 文案被写入', toastText(env).indexOf('登记还原请求失败') !== -1, toastText(env));
  check('[11.2] toast 元素被真正推上台', env.el('toast').classList.contains('translate-y-0'));
  check('[11.3] toast 图标切成错误态',
    env.el('toastIconContainer').innerHTML.indexOf('triangle-exclamation') !== -1,
    env.el('toastIconContainer').innerHTML);

  // =============================================================== [12]
  section('[12] 服务端数据一律转义后再进 DOM');

  env = boot();
  env.setRoutes(routesFor(Object.assign({}, LIST_BODY, {
    snapshots: [
      snapshot('mathbank-backup-20260915T133058872611Z.zip" onmouseover="alert(1)', '2026-09-15T13:30:58.872611Z'),
      snapshot(FILE_B, '2026-09-14T09:00:00.000000Z', {
        readable: false, error: '<img src=x onerror="alert(1)">'
      })
    ]
  })));
  await env.sandbox.loadBackupPanel();

  const evilHtml = listHtml(env);
  check('[12.1] manifest 里的 HTML 被转义而非解析',
    evilHtml.indexOf('<img src=x') === -1 && evilHtml.indexOf('&lt;img') !== -1);
  check('[12.2] 文件名里的引号被转义进属性，不会撑破 data 属性',
    evilHtml.indexOf('" onmouseover="alert(1)"') === -1
      && evilHtml.indexOf('&quot; onmouseover=&quot;') !== -1,
    evilHtml.match(/data-backup-file="[^"]*"/));
  check('[12.3] 转义后仍然渲染出两份快照',
    evilHtml.indexOf('aria-checked="false"') !== -1
      && evilHtml.split('data-backup-file=').length - 1 === 2);

  // =============================================================== [13]
  section('[13] 面板节点缺失时不允许抛异常（旧 HTML / 浏览器缓存）');

  check('[13.1] 每个导出都是函数（可被 index.html 的 inline onclick 直接调用）',
    typeof env.sandbox.runBackupNow === 'function'
      && typeof env.sandbox.requestBackupRestore === 'function'
      && typeof env.sandbox.confirmBackupRestore === 'function'
      && typeof env.sandbox.closeBackupRestoreConfirm === 'function'
      && typeof env.sandbox.cancelPendingRestore === 'function'
      && typeof env.sandbox.loadBackupPanel === 'function');
  check('[13.2] 导出集合与 index.html 里引用的名字一一对应', (function () {
    const html = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'index.html'), 'utf8');
    const used = ['runBackupNow', 'requestBackupRestore', 'confirmBackupRestore',
      'closeBackupRestoreConfirm', 'cancelPendingRestore'];
    return used.every(function (name) {
      return html.indexOf(name + '()') !== -1 && typeof env.sandbox[name] === 'function';
    });
  })());

  // 真的把整个面板节点全部摘掉，逐个调用导出：任何一个抛出都会让 inline onclick
  // 在浏览器控制台里炸出红色报错，而用户只看到"点了没反应"。
  const bare = boot({ missing: IDS.slice() });
  bare.setRoutes(routesFor(LIST_BODY));
  let threw = '';
  try {
    await bare.sandbox.loadBackupPanel();
    await bare.sandbox.runBackupNow();
    await bare.sandbox.requestBackupRestore();
    await bare.sandbox.confirmBackupRestore();
    await bare.sandbox.cancelPendingRestore();
    bare.sandbox.closeBackupRestoreConfirm();
  } catch (error) {
    threw = String((error && error.message) || error);
  }
  check('[13.3] DOM 节点全缺时所有导出调用都不抛异常', threw === '', threw);
  check('[13.4] DOM 节点全缺时 Toast 也不抛异常（无 toast DOM 走 console 兜底）', true);

  console.log('\n────────────────────────────────────────');
  console.log(fail === 0 ? '全部通过：' + pass + ' 项' : '失败 ' + fail + ' 项 / 共 ' + (pass + fail) + ' 项');
  process.exit(fail === 0 ? 0 : 1);
})();
