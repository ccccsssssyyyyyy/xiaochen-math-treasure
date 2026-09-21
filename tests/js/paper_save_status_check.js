/**
 * 组卷工作台「存档状态」端到端校验 —— 真实执行 paper.js，不靠正则猜源码。
 *
 * 为什么要有这个脚本：
 *   「草稿自动存本机」和「保存试卷写归档」是两件事，但界面上以前完全看不出来。
 *   用户组完卷关掉页面前，没有任何地方能确认到底有没有进试卷库；更糟的是
 *   /api/paper/save 每次都是 INSERT 一条新归档，误判「没存」就会重复存出好几份。
 * 所以这里用假 DOM 把 paper.js 整个加载进 vm 沙箱，真实驱动状态机，断言提示文案、
 *   基准是否被正确对齐/保留、以及刷新后会不会把已归档的卷面重新报成「未保存」。
 *
 * 第 [9] 节另覆盖导出行第四格「记作重做练习」：状态挂 store（面板整块重建 innerHTML，
 * 放 DOM 上会丢）、带卷面指纹（认领后又改题要显示回未认领）、认领成功后基准归位。
 *
 * 用法: node tests/js/paper_save_status_check.js [paper.js 路径]
 * 退出码: 0 全通过 / 1 有失败
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const paperPath = process.argv[2]
  || path.join(__dirname, '..', '..', 'static', 'js', 'paper.js');
const src = fs.readFileSync(paperPath, 'utf8');

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}
function section(title) { console.log('\n' + title); }

// ---------------------------------------------------------------- 假 DOM

function makeElement(id) {
  const attrs = {};
  const classes = new Set();
  return {
    id: id,
    innerHTML: '',
    textContent: '',
    innerText: '',
    value: '',
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
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: 1000, height: 2000, right: 1000, bottom: 2000 }; }
  };
}

function makeLocalStorage(store) {
  return {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; },
    clear: function () { Object.keys(store).forEach(function (k) { delete store[k]; }); },
    key: function (i) { return Object.keys(store)[i] === undefined ? null : Object.keys(store)[i]; },
    get length() { return Object.keys(store).length; }
  };
}

const STATUS_ID = 'paperSaveStatus';

/** 建一个装了真 paper.js 的沙箱。store 可复用，用来模拟「刷新页面」。 */
function boot(store) {
  const elements = {};
  function elementFor(id) { if (!elements[id]) elements[id] = makeElement(id); return elements[id]; }
  const documentHandlers = {};
  const requests = [];
  let fetchImpl = function () {
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ status: 'success' }); } });
  };

  const sandbox = {
    console: console,
    setTimeout: function () { return 0; },
    clearTimeout: function () {},
    setInterval: function () { return 0; },
    clearInterval: function () {},
    fetch: function (url, options) {
      requests.push({ url: String(url), options: options || {} });
      return fetchImpl(String(url), options || {});
    },
    localStorage: makeLocalStorage(store),
    document: {
      getElementById: elementFor,
      querySelector: function () { return null; },
      querySelectorAll: function () { return []; },
      createElement: function () { return makeElement('created'); },
      addEventListener: function (t, f) { (documentHandlers[t] = documentHandlers[t] || []).push(f); },
      removeEventListener: function () {},
      body: makeElement('body'),
      documentElement: { classList: { add: function () {}, remove: function () {} } },
      activeElement: null
    }
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  // api.js 在浏览器里先加载，提供 window.MathBankSafe；这里按同一份契约放一个替身，
  // 否则 paper.js 渲染卷面时会撞 undefined.sanitizeRichHtml。替身只管形状，不测消毒。
  sandbox.MathBankSafe = {
    escapeText: function (v) {
      return (v == null ? '' : String(v))
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    },
    escapeAttribute: function (v) {
      return (v == null ? '' : String(v))
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },
    sanitizePlainText: function (v) { return v == null ? '' : String(v); },
    sanitizeRichHtml: function (v) { return v == null ? '' : String(v); },
    safeClassList: function (v, fallback) { return fallback || ''; },
    safeImageUrl: function () { return ''; }
  };

  vm.createContext(sandbox);
// index.html 里 math-render.js 排在所有业务脚本之前，沙箱必须同一顺序：
// 题面正文的渲染口径就住在这个文件里，放替身等于把被测逻辑整个绕过。
require('./sandbox_base').loadBaseModules(sandbox);
  vm.runInContext(src, sandbox, { filename: 'paper.js' });

  return {
    sandbox: sandbox,
    store: store,
    requests: requests,
    statusNode: elementFor(STATUS_ID),
    canvasNode: elementFor('paperCanvasSection'),
    boot: function () { (documentHandlers['DOMContentLoaded'] || []).forEach(function (fn) { fn(); }); },
    setFetch: function (impl) { fetchImpl = impl; }
  };
}

function statusText(env) { return env.statusNode.textContent; }
function statusCls(env) { return env.statusNode.className || ''; }
function statusTip(env) { return env.statusNode.getAttribute('title') || ''; }

function ok(jsonBody) {
  return function () {
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(jsonBody); } });
  };
}
function httpError(status, jsonBody) {
  return function () {
    return Promise.resolve({ ok: false, status: status, json: function () { return Promise.resolve(jsonBody); } });
  };
}

// 时间标签有三种粒度：今天只给 HH:MM，昨天带「昨天」，更早带「M月D日」
const CLOCK = '(?:\\d{2}:\\d{2}|昨天 \\d{2}:\\d{2}|\\d{1,2}月\\d{1,2}日 \\d{2}:\\d{2})';
const SAVED_RE = new RegExp('^已保存( · ' + CLOCK + ')?$');
const DIRTY_RE = new RegExp('^有改动未保存( · 上次保存 ' + CLOCK + ')?$');

function fakeQuestion(id, type, content) {
  return {
    id: id, seq_num: id, question_type: type, difficulty: 'normal',
    content: content, knowledge_list: '函数'
  };
}

(async function main() {
  // ============================================================ [1] 初始未保存
  section('[1] 组装中的卷面必须先被报成「未保存」');
  const env = boot({});
  check('模块加载后导出了 window.saveCartToStorage（内联 onblur 靠它）',
    typeof env.sandbox.saveCartToStorage === 'function');
  check('模块加载后导出了 window.saveMetaToStorage（内联 onblur 靠它）',
    typeof env.sandbox.saveMetaToStorage === 'function');

  env.sandbox.PaperStore.questionsMap = {
    101: fakeQuestion(101, 'single_choice', '已知集合 A = {1,2}，则 A 的子集个数为（ ）'),
    102: fakeQuestion(102, 'detailed_answer', '求函数 f(x)=x^2-2x 在 [0,3] 上的最值。')
  };
  env.sandbox.PaperStore.cart = [{ id: 101, score: 5 }, { id: 102, score: 12 }];
  env.sandbox.PaperStore.meta.title = '成都高一数学周测';
  env.sandbox.saveCartToStorage();
  check('新组卷面显示「未保存 · 尚未写入试卷库」',
    statusText(env) === '未保存 · 尚未写入试卷库', '实际: ' + JSON.stringify(statusText(env)));
  check('未保存状态不是绿色（不能给人「已经存好了」的错觉）',
    statusCls(env).indexOf('amber') >= 0, statusCls(env));
  check('未保存状态带 tooltip 说明草稿与归档的区别',
    statusTip(env).indexOf('草稿') >= 0, statusTip(env));

  // ============================================================ [2] 状态节点真的在 DOM 里
  section('[2] 状态元素必须挂进控制面板（渲染后 DOM 里找得到）');
  try {
    env.sandbox.renderPaperCanvas();
    check('renderPaperCanvas 能跑通不抛错', true);
  } catch (e) {
    check('renderPaperCanvas 能跑通不抛错', false, e && e.message);
  }
  const canvasHtml = env.canvasNode.innerHTML;
  check('渲染出的控制面板里带 id="paperSaveStatus"', canvasHtml.indexOf('id="paperSaveStatus"') >= 0);
  check('渲染出的控制面板里带静态副说明（草稿自动存本机）',
    canvasHtml.indexOf('草稿自动存本机') >= 0);
  check('状态提示和「保存试卷」按钮渲染在同一块面板里',
    canvasHtml.indexOf('savePaperToDb()') >= 0 && canvasHtml.indexOf('id="paperSaveStatus"') >= 0);
  check('重绘之后状态文案按卷面重算（不是空白）',
    statusText(env) === '未保存 · 尚未写入试卷库', '实际: ' + JSON.stringify(statusText(env)));

  // ============================================================ [3] 保存成功
  section('[3] 保存成功后转为「已保存」并记下时间');
  env.setFetch(ok({ status: 'success', paper_id: 9 }));
  await env.sandbox.savePaperToDb();
  check('显示「已保存 · HH:MM」', SAVED_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));
  check('状态转绿', statusCls(env).indexOf('emerald') >= 0, statusCls(env));
  const snapRaw = env.store['mathbank_paper_saved_snapshot'];
  check('基准快照已落到 localStorage（刷新后不会误报未保存）', typeof snapRaw === 'string' && snapRaw.length > 0);
  let snap = null;
  try { snap = JSON.parse(snapRaw); } catch (e) { }
  check('快照里带 sig 与 at', !!snap && typeof snap.sig === 'string' && typeof snap.at === 'string',
    JSON.stringify(snap));
  const sigAfterSave = snap ? snap.sig : '';
  const saveReqs = env.requests.filter(function (r) { return r.url === '/api/paper/save'; });
  check('保存请求打到了 /api/paper/save', saveReqs.length === 1, String(saveReqs.length));
  const saveBody = JSON.parse(saveReqs[0].options.body);
  check('请求体带上卷面题目与分值',
    saveBody.questions.length === 2 && saveBody.questions[0].score === 5 && saveBody.questions[1].score === 12,
    JSON.stringify(saveBody.questions));

  // ============================================================ [4] 改分数→脏
  section('[4] 只改一处分值就要报「有改动未保存」');
  env.sandbox.PaperStore.cart[0].score = 8;
  env.sandbox.saveCartToStorage();
  check('显示「有改动未保存 · 上次保存 HH:MM」', DIRTY_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));
  check('脏状态不是绿色', statusCls(env).indexOf('amber') >= 0, statusCls(env));
  check('提示写明会另存一条新归档、不覆盖旧的', statusTip(env).indexOf('新归档') >= 0, statusTip(env));

  // ============================================================ [5] 改回→干净
  section('[5] 改回原值后要恢复「已保存」（指纹可逆，不残留假脏标记）');
  env.sandbox.PaperStore.cart[0].score = 5;
  env.sandbox.saveCartToStorage();
  check('显示「已保存」', SAVED_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));

  // ============================================================ [6] 改留白不算脏
  section('[6] 改留白不该被报成未保存（留白没进数据库，改它不会让归档变旧）');
  env.sandbox.updateGlobalSolutionSpace('0.0');
  check('改完留白仍是「已保存」', SAVED_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));
  check('留白确实写进了卷面草稿（只是不作为归档依据）',
    env.sandbox.PaperStore.cart[0].solution_space === '0.0',
    String(env.sandbox.PaperStore.cart[0].solution_space));

  // ============================================================ [7] 元数据改动
  section('[7] 在卷面上直接改标题 / 副标题 / 开关标记也要立刻转脏');
  env.sandbox.updatePaperMeta('title', '成都高一数学周测（修订）');
  check('改标题后显示「有改动未保存」', DIRTY_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));
  env.sandbox.updatePaperMeta('title', '成都高一数学周测');
  check('标题改回去后恢复「已保存」', SAVED_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));
  env.sandbox.updatePaperMeta('show_notice', false);
  check('关掉注意事项也算改动', DIRTY_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));
  env.sandbox.updatePaperMeta('show_notice', true);
  check('恢复注意事项后回到「已保存」', SAVED_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));

  // ============================================================ [8] 保存中
  section('[8] 请求往返期间必须显示「保存中…」，不能停在旧状态');
  env.sandbox.PaperStore.cart[0].score = 9;
  env.sandbox.saveCartToStorage();
  let release = null;
  env.setFetch(function () {
    return new Promise(function (resolve) {
      release = function () {
        resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ status: 'success', paper_id: 10 }); } });
      };
    });
  });
  const inflight = env.sandbox.savePaperToDb();
  check('发出请求后立刻显示「保存中…」', statusText(env) === '保存中…', '实际: ' + JSON.stringify(statusText(env)));
  release();
  await inflight;
  check('响应回来后转为「已保存」', SAVED_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));

  // ============================================================ [9] 保存失败
  section('[9] 保存失败必须显式报错，并且不能把基准往前推');
  env.sandbox.PaperStore.cart[0].score = 15;
  env.sandbox.saveCartToStorage();
  env.setFetch(httpError(400, { status: 'error', message: '试卷中包含已删除或不存在的题目。' }));
  await env.sandbox.savePaperToDb();
  check('显示「保存失败，请重试」', statusText(env) === '保存失败，请重试', '实际: ' + JSON.stringify(statusText(env)));
  check('失败状态是红色', statusCls(env).indexOf('red') >= 0, statusCls(env));
  check('tooltip 带出后端原始报错', statusTip(env).indexOf('已删除') >= 0, statusTip(env));
  env.sandbox.PaperStore.lastSaveError = '';
  env.sandbox.saveCartToStorage();
  check('失败后基准仍停在上一版，卷面继续报「有改动未保存」',
    DIRTY_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));

  // ============================================================ [10] 空卷面
  section('[10] 空卷面不显示任何存档字样（避免「未保存」的假警报）');
  env.sandbox.PaperStore.cart = [];
  env.sandbox.saveCartToStorage();
  check('清空后状态文案为空', statusText(env) === '', '实际: ' + JSON.stringify(statusText(env)));
  check('清空后不留 tooltip', statusTip(env) === '', statusTip(env));

  // ============================================================ [11] 载入历史归档
  section('[11] 载入历史试卷后基准要对齐到那份归档，不能报「未保存」');
  env.sandbox.PaperStore.cart = [{ id: 101, score: 5 }];
  env.sandbox.PaperStore.meta.title = '待替换的草稿';
  env.sandbox.saveCartToStorage();
  check('前置：这份草稿确实是脏的',
    DIRTY_RE.test(statusText(env)) || statusText(env) === '未保存 · 尚未写入试卷库',
    '实际: ' + JSON.stringify(statusText(env)));

  const renderCalls = [];
  env.sandbox.renderPaperWorkspace = function () { renderCalls.push(1); };
  const archivedAt = '2026-09-13T07:02:00Z';
  env.setFetch(ok({
    status: 'success',
    data: {
      id: 7, title: '旧卷·函数与导数', subtitle: '9 月归档', paper_type: 'exam',
      show_notice: true, show_secret: true, created_at: archivedAt,
      questions: [{ id: 102, score: 10, question: fakeQuestion(102, 'detailed_answer', '旧卷题干') }]
    }
  }));
  await env.sandbox.loadSavedPaper(7);
  check('载入后显示「已保存」', SAVED_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));
  const archDate = new Date(archivedAt);
  const pad2 = function (n) { return n < 10 ? '0' + n : String(n); };
  const archHHMM = pad2(archDate.getHours()) + ':' + pad2(archDate.getMinutes());
  check('载入后的基准取自归档创建时间（不是「刚刚」）',
    statusText(env).indexOf(archHHMM) >= 0, '期望含 ' + archHHMM + '，实际: ' + statusText(env));
  check('载入的卷面已落盘到 localStorage（刷新不再退回旧草稿）',
    env.store['mathbank_paper_cart'] === JSON.stringify([{ id: 102, score: 10 }]),
    String(env.store['mathbank_paper_cart']));
  check('载入的标题也落盘了', String(env.store['mathbank_paper_meta']).indexOf('旧卷·函数与导数') >= 0);
  check('载入后仍会重绘工作区', renderCalls.length === 1, String(renderCalls.length));
  env.sandbox.PaperStore.cart[0].score = 1;
  env.sandbox.saveCartToStorage();
  check('载入后再改动，重新转脏', DIRTY_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));
  env.sandbox.PaperStore.cart[0].score = 10;
  env.sandbox.saveCartToStorage();
  check('载入的卷面改回归档原样则回到「已保存」',
    SAVED_RE.test(statusText(env)), '实际: ' + JSON.stringify(statusText(env)));

  // ============================================================ [12] 刷新后不误报
  section('[12] 刷新页面后已归档的卷面必须仍显示「已保存」（否则会重复存出多份归档）');
  const env2 = boot(env.store);
  env2.sandbox.PaperStore.questionsMap = env.sandbox.PaperStore.questionsMap;
  check('刷新前 localStorage 里有草稿和基准快照',
    typeof env.store['mathbank_paper_cart'] === 'string'
    && typeof env.store['mathbank_paper_saved_snapshot'] === 'string');
  env2.boot();
  check('刷新后草稿被恢复', env2.sandbox.PaperStore.cart.length === 1,
    JSON.stringify(env2.sandbox.PaperStore.cart));
  check('刷新后基准被恢复（不是 null）',
    typeof env2.sandbox.PaperStore.savedSignature === 'string' && env2.sandbox.PaperStore.savedSignature.length > 0);
  check('刷新后显示「已保存」而不是误报未保存', SAVED_RE.test(statusText(env2)),
    '实际: ' + JSON.stringify(statusText(env2)));
  check('刷新后时间标签仍是归档那一刻，不会自己变成「刚刚」',
    statusText(env2).indexOf(archHHMM) >= 0, '期望含 ' + archHHMM + '，实际: ' + statusText(env2));
  env2.sandbox.PaperStore.cart[0].score = 3;
  env2.sandbox.saveCartToStorage();
  check('刷新后改动仍能转脏', DIRTY_RE.test(statusText(env2)), '实际: ' + JSON.stringify(statusText(env2)));

  // ============================================================ [13] 指纹覆盖面
  section('[13] 指纹要覆盖归档实际存的东西：题号集合与题序');
  const env3 = boot({});
  env3.sandbox.PaperStore.questionsMap = {
    101: fakeQuestion(101, 'single_choice', 'A'), 102: fakeQuestion(102, 'single_choice', 'B'),
    103: fakeQuestion(103, 'single_choice', 'C')
  };
  env3.setFetch(ok({ status: 'success', paper_id: 11 }));
  env3.sandbox.PaperStore.cart = [{ id: 101, score: 5 }, { id: 102, score: 5 }];
  await env3.sandbox.savePaperToDb();
  check('两题卷面已存好', SAVED_RE.test(statusText(env3)), '实际: ' + JSON.stringify(statusText(env3)));
  env3.sandbox.PaperStore.cart.push({ id: 103, score: 5 });
  env3.sandbox.saveCartToStorage();
  check('加题后转脏', DIRTY_RE.test(statusText(env3)), '实际: ' + JSON.stringify(statusText(env3)));
  env3.sandbox.PaperStore.cart = [{ id: 101, score: 5 }, { id: 102, score: 5 }];
  env3.sandbox.saveCartToStorage();
  check('删回原样后恢复「已保存」', SAVED_RE.test(statusText(env3)), '实际: ' + JSON.stringify(statusText(env3)));
  env3.sandbox.movePaperQuestion(1, 'up');
  check('调换题序后转脏（order_index 会进库）', DIRTY_RE.test(statusText(env3)), '实际: ' + JSON.stringify(statusText(env3)));
  env3.sandbox.PaperStore.cart.reverse();
  env3.sandbox.saveCartToStorage();
  check('顺序调回后恢复「已保存」', SAVED_RE.test(statusText(env3)), '实际: ' + JSON.stringify(statusText(env3)));
  env3.sandbox.PaperStore.meta.paper_type = 'quiz';
  env3.sandbox.saveMetaToStorage();
  check('换试卷模板后转脏', DIRTY_RE.test(statusText(env3)), '实际: ' + JSON.stringify(statusText(env3)));

  // ============================================================ [9] 认领入口：导出行第四格
  section('[9] 导出行第四格：认领入口与已认领状态');
  const envAdopt = boot({});
  envAdopt.sandbox.PaperStore.questionsMap = {
    101: fakeQuestion(101, 'single_choice', '已知集合 A = {1,2}，则 A 的子集个数为（ ）'),
    102: fakeQuestion(102, 'detailed_answer', '求函数 f(x)=x^2-2x 在 [0,3] 上的最值。')
  };
  envAdopt.sandbox.PaperStore.cart = [{ id: 101, score: 5 }, { id: 102, score: 12 }];
  envAdopt.sandbox.PaperStore.meta.title = '成都高一数学周测';
  envAdopt.sandbox.renderPaperCanvas();
  let adoptHtml = envAdopt.canvasNode.innerHTML;

  check('未认领时第四格是「记作重做练习」，onclick 指向 adoptPaperAsRedo',
    adoptHtml.indexOf('id="paperRedoAdoptBtn"') >= 0
    && adoptHtml.indexOf('onclick="adoptPaperAsRedo()"') >= 0
    && adoptHtml.indexOf('记作重做练习') >= 0);
  check('导出行里不再有「导出后弹窗认领」的残留触发点',
    adoptHtml.indexOf('maybePromptRedoAdopt') < 0);
  check('导出行仍是四格（PDF / Word / LaTeX / 认领）',
    adoptHtml.indexOf("exportPaperPdf('paper')") >= 0
    && adoptHtml.indexOf('exportPaperWord()') >= 0
    && adoptHtml.indexOf('exportPaperBundle()') >= 0
    && adoptHtml.indexOf('id="paperRedoAdoptBtn"') >= 0);

  // 面板每次改动都整块重建 innerHTML —— 状态必须挂 store，并且带卷面指纹
  envAdopt.sandbox.PaperStore.redoAdopt = {
    paperId: 12, round: 2, pooledCount: 2, questionCount: 3,
    signature: '101,102', adopted: true
  };
  envAdopt.sandbox.renderPaperCanvas();
  adoptHtml = envAdopt.canvasNode.innerHTML;
  check('已认领且指纹一致时，第四格变绿为「已认领 · 第 2 轮」',
    adoptHtml.indexOf('已认领 · 第 2 轮') >= 0
    && adoptHtml.indexOf('openRedoPaperFromLibrary(12)') >= 0
    && adoptHtml.indexOf('emerald') >= 0);
  check('已认领后不再挂认领动作（改挂「去录入对错」）',
    adoptHtml.indexOf('onclick="adoptPaperAsRedo()"') < 0);

  // 认领之后又改题 → 指纹对不上。界面写着已认领、实际录进去的却是另一份题序，比不显示更糟。
  envAdopt.sandbox.PaperStore.questionsMap[103] = fakeQuestion(103, 'fill_in_blank', '若 x+1=3，则 x= ?');
  envAdopt.sandbox.PaperStore.cart.push({ id: 103, score: 5 });
  envAdopt.sandbox.renderPaperCanvas();
  adoptHtml = envAdopt.canvasNode.innerHTML;
  check('认领后又改了卷面 → 指纹不符，显示回未认领',
    adoptHtml.indexOf('onclick="adoptPaperAsRedo()"') >= 0
    && adoptHtml.indexOf('已认领') < 0);

  // 卷面里没有有效内容时不能点（totalCount 为 0）
  envAdopt.sandbox.PaperStore.questionsMap[104] = fakeQuestion(104, 'single_choice', '');
  envAdopt.sandbox.PaperStore.cart = [{ id: 104, score: 5 }];
  let emptyOk = true, emptyErr = '';
  try { envAdopt.sandbox.renderPaperCanvas(); } catch (e) { emptyOk = false; emptyErr = e && e.message; }
  check('卷面无有效内容时第四格置灰不可点',
    emptyOk && envAdopt.canvasNode.innerHTML.indexOf('id="paperRedoAdoptBtn" disabled') >= 0,
    emptyErr);

  // 真点一次：POST /api/redo/adopt，未落库的卷面带卷面过去现场建卷；成功后就地变绿、不跳页
  envAdopt.sandbox.PaperStore.cart = [{ id: 101, score: 5 }, { id: 102, score: 12 }];
  envAdopt.sandbox.renderPaperCanvas();
  envAdopt.requests.length = 0;
  envAdopt.setFetch(ok({
    status: 'success', paper_id: 12, title: '成都高一数学周测', created: false,
    already: false, redo_round: 2, question_count: 3, pooled_count: 2,
    message: '已记作重做练习。'
  }));
  await envAdopt.sandbox.adoptPaperAsRedo();
  const adoptReq = envAdopt.requests.filter(function (r) { return r.url === '/api/redo/adopt'; })[0];
  check('点第四格走 POST /api/redo/adopt', !!adoptReq && adoptReq.options.method === 'POST');
  const adoptBody = adoptReq ? JSON.parse(adoptReq.options.body) : {};
  check('未落库的卷面把卷面带过去让后端现场建卷',
    !adoptBody.paper_id && adoptBody.title === '成都高一数学周测'
    && Array.isArray(adoptBody.questions) && adoptBody.questions.length === 2,
    JSON.stringify(adoptBody).slice(0, 160));
  check('认领成功后状态写回 store（面板重绘不丢）',
    !!envAdopt.sandbox.PaperStore.redoAdopt
    && envAdopt.sandbox.PaperStore.redoAdopt.paperId === 12
    && envAdopt.sandbox.PaperStore.redoAdopt.signature === '101,102');
  check('认领成功后把「卷面 = 库里这张卷」的基准归位（防再点一次重复建卷）',
    envAdopt.sandbox.PaperStore.loadedPaperId === 12
    && envAdopt.sandbox.PaperStore.loadedPaperFingerprint === '101,102');
  check('认领成功后第四格就地变绿（不跳页）',
    envAdopt.canvasNode.innerHTML.indexOf('已认领 · 第 2 轮') >= 0);

  check('两次不同卷面的指纹互不相同', sigAfterSave.length > 0);

  console.log('\n通过 ' + pass + ' 项，失败 ' + fail + ' 项');
  process.exit(fail === 0 ? 0 : 1);
})().catch(function (err) {
  console.log('\n沙箱执行抛出异常：\n' + (err && err.stack ? err.stack : String(err)));
  process.exit(1);
});
