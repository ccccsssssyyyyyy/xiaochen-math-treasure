/**
 * 错题本审校页端到端校验 —— 真实执行 mistake.js（无构建 IIFE，vm + 假 DOM）。
 *
 * 这一层最安静的错法是「入库口径对不上勾选口径」：审校页左边只列了用户勾进错题本
 * 的那几道，可后端批量入库的**默认口径是「本批次全部已识别、尚未入库的题」**。不显式
 * 带 record_ids，用户就会在「只看了 3 道」的情况下把整批 40 道都灌进题库和试题篮 ——
 * 请求返回 200、toast 说成功，没有任何一处会报错。
 *
 * 所以这里断言两件事：
 *   1) 门禁真的挡人：题面为空时**不发**入库请求，而不是发出去让后端产出占位题面；
 *   2) 放行时请求体里 record_ids 恰等于「本次收录」的那几个 id，不多不少。
 * 另外覆盖：左列表口径、状态角标、编辑防抖保存的字段名、识别补跑只带缺的那几道。
 *
 * 用法: node tests/js/mistake_review_payload_check.js [mistake.js 路径]
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

// 假 DOM 缺一个方法，异常就会被 promise 链吞掉、只剩一句没头没尾的 FAIL。挂上钩子。
process.on('unhandledRejection', function (err) {
  console.error('  [未处理的拒绝] ' + (err && err.stack ? err.stack : err));
  fail++;
});
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}

// ---------------------------------------------------------------- 假 DOM

function makeElement(id) {
  const classes = new Set();
  return {
    id: id,
    innerHTML: '',
    textContent: '',
    value: '',
    // 光标位。补图插在「用户光标停的位置」，夹具必须给得出这两个值。
    selectionStart: 0,
    selectionEnd: 0,
    className: '',
    disabled: false,
    hidden: false,
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
    removeEventListener: function (type, fn) {
      const list = this.handlers[type] || [];
      const i = list.indexOf(fn);
      if (i >= 0) list.splice(i, 1);
    },
    querySelector: function () { return null; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: 1000, height: 2000, right: 1000, bottom: 2000 }; }
  };
}

const elements = {};
function elementFor(id) {
  if (!elements[id]) elements[id] = makeElement(id);
  return elements[id];
}

const requests = [];
const documentHandlers = {};
const timers = [];
const intervals = {};
let timerSeq = 1;
let intervalSeq = 1;
// 任务状态可按序脚本化：pollTask 的 setInterval 默认不自动跑，由用例手动 runIntervals()
// 逐次推进，这样才卡得住「识别到一半」这个中间态。
let taskScript = [];
// 服务端记录快照。改成识别场景后，这里要在两次轮询之间换内容，模拟逐题落库。
let serverRecords = null;
let cartCalls = [];
let workspaceCalls = [];
let cropCalls = [];
// 解析截图 OCR：ocrScript 按序脚本化 /api/ocr 的返回，ocrCalls 记录识别请求。
let ocrScript = [];
let ocrCalls = [];
let detailPages = null;
// 记录 PUT 的「往返窗口」开关。默认 false（立即 resolve，与真实网络一样快但不可控）；
// 打开后 PUT 会挂在 pendingPuts 里，由用例自己 release —— 这样才能卡住「请求已发出、
// 响应还没回来」那一瞬间去检查屏幕，也就是保存回滚 bug 唯一的存活窗口。
let deferPuts = false;
let pendingPuts = [];
// 让记录 PUT 返回失败（模拟网络断了 / 服务重启 / 400）。
let failPuts = false;
// 让匹配到的 URL 在网络层直接 reject（fetch 抛 TypeError），而不是回一个 ok:false 的响应。
// 这是两种不同的失败：ok:false 会走 .then 里的分支，reject 只走 .catch —— 后者没写
// 拒绝分支时，「先置灰、再在 .then 里恢复」的按钮就永久卡死。
let rejectPatterns = [];

function releasePuts() {
  const queued = pendingPuts.splice(0, pendingPuts.length);
  queued.forEach(function (item) { item.release(); });
  return queued;
}

function runTimers() {
  const queued = timers.splice(0, timers.length);
  queued.forEach(function (timer) { timer.fn(); });
}

function runIntervals() {
  Object.keys(intervals).forEach(function (id) { intervals[id](); });
}

function jsonResponse(ok, status, data) {
  return Promise.resolve({ ok: ok, status: status, json: function () { return Promise.resolve(data); } });
}

// 记录夹具：字段按后端 to_dict() 的口径给全，审校页只用其中一部分，
// 但 refreshMistakeDetail → renderCards 会吃整份，缺字段会在 promise 链里炸。
function rec(id, extra) {
  return Object.assign({
    id: id, page_no: 1, block_index: id, block_x_start: 0.0, block_x_end: 0.49,
    block_y_start: 0.05, block_y_end: 0.14, column_index: 1,
    grad_status: 'incorrect', error_reason: '', include_in_handout: false,
    answer_source: '', answer_reviewed: false, image_block: '/b0' + id + '.png',
    recognize_status: 'pending', merge_id: '', merged_block_count: 1,
    block_images: [], figure_images: [], answer_images: [],
    question_no: '', content: '', answer_markdown: '', question_type: 'detailed_answer',
    question_id: null, error_reasons: [],
    // 分类六列（v1010）：后端 to_dict 的口径，缺了会让审校页读到 undefined
    source: '', category_compulsory: '', category_chapter: '', category_knowledge: '',
    knowledge_tags: '', solve_method: '', tags: '', related_curriculums: []
  }, extra || {});
}

// 教材树（按学科）：审校页的 学段/章节/小节 与 关联章节 下拉都靠它填充。
// 三科是两棵独立教材树 —— 混用会让物化题的章节下拉里全是数学章节。
const CURRICULUM_TREES = {
  math: {
    '必修第一册': {
      '第一章 集合与常用逻辑用语': ['集合的概念', '集合间的基本关系'],
      '第二章 一元二次函数': ['函数的概念', '函数的单调性']
    }
  },
  physics: {
    '必修第一册': {
      '第一章 运动的描述': ['质点 参考系', '时间 位移'],
      '第二章 匀变速直线运动': ['速度与时间的关系']
    }
  }
};

// 分类信息用例专用记录：31 的分类全部落在教材树上，32 故意放一个「树上没有」的章节。
const MATH_RECORDS = [
  rec(31, {
    question_no: '7', include_in_handout: true, recognize_status: 'done',
    content: '第四题，求导。', answer_markdown: '第五步，答案是 2。',
    question_type: 'single_choice', difficulty: 'easy_error',
    source: '2025 全国甲卷',
    category_compulsory: '必修第一册',
    category_chapter: '第一章 集合与常用逻辑用语',
    category_knowledge: '集合的概念',
    knowledge_tags: '集合, 交集', solve_method: '定义法', tags: '高一, 期中',
    related_curriculums: [{
      compulsory: '必修第一册',
      chapter: '第一章 集合与常用逻辑用语',
      knowledge: '集合间的基本关系'
    }]
  }),
  rec(32, {
    question_no: '8', include_in_handout: true, recognize_status: 'done',
    content: '第六题，求最值。',
    category_compulsory: '必修第一册',
    // 换版本前的大纲 / 当年 AI 标的章节：不在当前教材树上，但**绝不能丢**
    category_chapter: '第三章 函数', category_knowledge: '函数的单调性'
  })
];

const BASE_RECORDS = [
  // 11：勾进错题本，但识别没跑过 → 题面为空，必须被门禁挡下
  rec(11, { question_no: '1', include_in_handout: true, content: '', recognize_status: 'pending' }),
  // 12：没勾进错题本。审校页不该出现它，入库也不该带上它。
  rec(12, { question_no: '2', include_in_handout: false, content: '未勾选的题面', recognize_status: 'done' }),
  // 13：勾了且识别完成 → 正常放行
  rec(13, { question_no: '3', include_in_handout: true, content: '已知 $a>0$，求 $a+\\frac{1}{a}$ 的最小值。', recognize_status: 'done' }),
  // 14：勾了、题面有、且已入库 → 角标应为「已入库」
  rec(14, { question_no: '4', include_in_handout: true, content: '已入库的题', recognize_status: 'done', question_id: 88 })
];

function echoDetail() {
  return {
    batch: { id: 7, subject: 'physics', title: '测试卷', page_count: 1 },
    records: (serverRecords || BASE_RECORDS).map(function (r) { return Object.assign({}, r); }),
    pages: detailPages || [{
      page_no: 1, url: '/page_1.png', width: 1000, height: 2000,
      layout: { mode: 'single', boundary: 0.5, source: 'text_layer', confidence: 1.0 },
      column_boundary: 0.5, snap_points: [], gap_candidates: [],
      column_snap_points: {}, column_gap_candidates: {},
      manual_boxes: [], manual_merges: [], hidden_blocks: []
    }],
    question_types: [], mistake_reasons: []
  };
}

const sandbox = {
  console: console,
  setTimeout: function (fn, delay) { const id = timerSeq++; timers.push({ id: id, fn: fn, delay: delay || 0 }); return id; },
  clearTimeout: function (id) { const i = timers.findIndex(function (t) { return t.id === id; }); if (i >= 0) timers.splice(i, 1); },
  setInterval: function (fn) { const id = intervalSeq++; intervals[id] = fn; return id; },
  clearInterval: function (id) { delete intervals[id]; },
  confirm: function () { return true; },
  fetch: function (url, options) {
    const opts = options || {};
    requests.push({ url: url, options: opts });
    if (rejectPatterns.length && rejectPatterns.some(function (re) { return re.test(url); })) {
      return Promise.reject(new TypeError('Failed to fetch'));
    }
    if (/\/import-to-bank$/.test(url)) {
      const body = JSON.parse(opts.body || '{}');
      const ids = body.record_ids || [];
      return jsonResponse(true, 200, {
        status: 'success', imported: ids.length, already: 0, skipped: [], duplicates: [],
        imported_items: ids.map(function (id) {
          return { question_id: 1000 + id, question_type: 'detailed_answer' };
        })
      });
    }
    if (/^\/api\/mistakes\/records\/\d+$/.test(url)) {
      const id = Number(url.split('/').pop());
      const pool = serverRecords || BASE_RECORDS;
      if (failPuts) {
        return jsonResponse(false, 500, { status: 'error', message: '保存失败（夹具）' });
      }
      const base = pool.filter(function (r) { return r.id === id; })[0] || {};
      const merged = Object.assign({}, base, JSON.parse(opts.body || '{}'));
      // 服务端快照跟着一起改 —— 本地与服务端同源，后续任何一次详情拉取都不会
      // 把用例刚改出来的字段抹回去（否则「补图后预览有图」这类断言是假绿）。
      const index = pool.findIndex(function (r) { return r.id === id; });
      if (index >= 0) pool[index] = merged;
      const response = jsonResponse(true, 200, { status: 'success', record: merged });
      // 挂起点在「服务端已改、响应还没到」之间 —— 落库发生在 release 那一刻，
      // 与真实顺序一致（否则用例可能拿「还没写库」的状态做出错误结论）。
      if (deferPuts) {
        return new Promise(function (resolve) {
          pendingPuts.push({ id: id, body: JSON.parse(opts.body || '{}'), release: function () { resolve(response); } });
        });
      }
      return response;
    }
    if (/\/crop-figure$/.test(url)) {
      cropCalls.push({ url: url, body: JSON.parse(opts.body || '{}') });
      return jsonResponse(true, 200, {
        status: 'success',
        image_path: '/static/uploads/mistakes/7/figures/crop_test.png',
        width: 320, height: 140
      });
    }
    if (/\/recognize$/.test(url)) {
      return jsonResponse(true, 200, { status: 'success', task_id: 1, record_count: 1 });
    }
    if (/^\/api\/ocr$/.test(url)) {
      ocrCalls.push({ url: url, body: opts.body, options: opts });
      // 默认回一份能用的解析；要测失败/空结果就用例里改 ocrScript。
      if (ocrScript.length) return jsonResponse(true, 200, ocrScript.shift());
      return jsonResponse(true, 200, { status: 'success', latex: '由图像识别得到的解析。', confidence: 0.99 });
    }
    if (/^\/api\/categories/.test(url)) {
      const matched = /subject=([^&]+)/.exec(url);
      const subject = matched ? decodeURIComponent(matched[1]) : 'math';
      return jsonResponse(true, 200, CURRICULUM_TREES[subject] || {});
    }
    if (/^\/api\/tasks\//.test(url)) {
      if (taskScript.length) return jsonResponse(true, 200, taskScript.shift());
      return jsonResponse(true, 200, { status: 'completed', block_count: 1, recognized: 1, failed: 0, steps: [] });
    }
    if (/^\/api\/mistakes\/batches\/\d+$/.test(url)) {
      return jsonResponse(true, 200, echoDetail());
    }
    return jsonResponse(true, 200, { status: 'success' });
  },
  // 解析截图 OCR 会 new FileReader()/AbortController()/FormData()。
  // FileReader 同步回调 onload：真实实现也是「先出缩略图、再等 fetch 回来」，
  // 这里只关心缩略图有没有上屏，同步即可。
  FileReader: function () {
    this.onload = null;
    this.result = '';
    this.readAsDataURL = function (file) {
      const url = (file && file.__dataUrl) || 'data:image/png;base64,AAAA';
      this.result = url;
      if (this.onload) this.onload({ target: { result: url } });
    };
  },
  AbortController: function () {
    this.signal = { aborted: false };
    this.abort = function () { this.signal.aborted = true; };
  },
  FormData: function () {
    const bag = {};
    this.append = function (k, v) { bag[k] = v; };
    this.get = function (k) { return bag[k]; };
    this.has = function (k) { return Object.prototype.hasOwnProperty.call(bag, k); };
    this._bag = bag;
  },
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  MathBankModal: { open: function () {}, close: function () {} },
  // 框选弹窗会在 window 上挂 resize（视图尺寸变了要重算自适应）。
  addEventListener: function (type, fn) { (documentHandlers[type] = documentHandlers[type] || []).push(fn); },
  removeEventListener: function () {},
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
// 沙箱也要有页面里先加载的基础模块，否则 window.MathRender 是 undefined
require('./sandbox_base').loadBaseModules(sandbox);
vm.runInContext(src, sandbox, { filename: 'mistake.js' });

// selectWorkspace 被 mistake.js 自己挂到 window 上了，加载后覆写成探针。
sandbox.addManyToCart = function (pairs) { cartCalls.push(pairs); return pairs.length; };
sandbox.selectWorkspace = function (id, name) { workspaceCalls.push([id, name]); };

const store = sandbox.MistakeStore;

// 「识别未完成的 N 道」写进的是按钮里的 span，假 DOM 的 querySelector 默认返回 null，
// 这里给它一个可读的替身，否则那条文案永远断言不到（正是「半截通过」的典型来源）。
const recBtnLabel = elementFor('mrRecognizeBtnLabel');
elementFor('mrRecognizeBtn').querySelector = function () { return recBtnLabel; };

function fireDocument(type, payload) {
  (documentHandlers[type] || []).slice().forEach(function (fn) { fn(payload || {}); });
}
fireDocument('DOMContentLoaded');

function flush() {
  return new Promise(function (resolve) {
    setImmediate(function () {
      runTimers();
      setImmediate(resolve);
    });
  });
}

function importPosts() {
  return requests.filter(function (r) { return /\/import-to-bank$/.test(r.url); });
}
function lastImportIds() {
  const posts = importPosts();
  if (!posts.length) return null;
  return JSON.parse(posts[posts.length - 1].options.body || '{}').record_ids || null;
}
function recognizePosts() {
  return requests.filter(function (r) { return /\/recognize$/.test(r.url); });
}
function lastRecognizeIds() {
  const posts = recognizePosts();
  if (!posts.length) return null;
  return JSON.parse(posts[posts.length - 1].options.body || '{}').record_ids || null;
}
function detailGets() {
  return requests.filter(function (r) { return /^\/api\/mistakes\/batches\/7$/.test(r.url); });
}
function recordPuts() {
  return requests.filter(function (r) { return /^\/api\/mistakes\/records\/\d+$/.test(r.url); });
}
function textOf(id) {
  const el = elementFor(id);
  return String(el.innerHTML || '') + ' ' + String(el.textContent || '');
}

function reset(records) {
  // 先正常退出审校页：这一步会把模块私有的「当前草稿」清空，之后再换记录集就不会
  // 出现「拿 A 题的 textarea 内容去保存 B 题」的夹具自污染。必须放在替换 store.records
  // 之前 —— 那样这次 flush 面对的仍是上一轮的记录与文案，天然是空跑。
  // 识别态必须先复位：closeMistakeReview 会看 active 决定要不要补拉详情，
  // 上一轮留着的 active=true 会让这次 reset 自己发出一个请求。
  store.recognizeLive.active = false;
  store.recognizeLive.queue = [];
  store.recognizeLive.done = [];
  store.recognizeLive.current = null;
  store.recognizeLive.errors = [];
  sandbox.closeMistakeReview();
  requests.length = 0;
  timers.length = 0;
  cartCalls = [];
  workspaceCalls = [];
  store.batch = { id: 7, subject: 'physics', title: '测试卷', page_count: 1 };
  // 服务端快照与本地记录保持同一份 —— 真实系统里这就是同一个库。两者不一致时，
  // 任何一次详情拉取都会把用例刚设好的本地记录抹回去，用例就变成假绿：
  // 比如「题面为空」的门禁先一步拦下，于是根本测不到后面那道门禁。
  const snapshot = (records || BASE_RECORDS).map(function (r) { return Object.assign({}, r); });
  serverRecords = snapshot.map(function (r) { return Object.assign({}, r); });
  store.records = snapshot;
  store.pages = echoDetail().pages;
  store.selectedRecordId = null;
  ['mistakeListView', 'mistakeDetailView', 'mistakeReviewView'].forEach(function (id) {
    elementFor(id).classList.remove('hidden');
  });
  elementFor('mrContent').value = '';
  elementFor('mrAnswer').value = '';
  elementFor('mrList').innerHTML = '';
  elementFor('mrGateText').textContent = '';
  elementFor('mrGateBar').classList.add('hidden');
  elementFor('toastMessage').textContent = '';
  elementFor('toastMessage').innerHTML = '';
  elementFor('mrContent').disabled = false;
  elementFor('mrAnswer').disabled = false;
  elementFor('mrLockHint').classList.add('hidden');
  elementFor('mrLockHintText').textContent = '';
  elementFor('mrRecognizeBtn').disabled = false;
  elementFor('mrSaveState').textContent = '';
  Object.keys(intervals).forEach(function (id) { delete intervals[id]; });
  taskScript = [];
  // 上一轮若留着挂起的 PUT，会跨用例污染下一轮的请求账本与状态。
  deferPuts = false;
  pendingPuts = [];
  failPuts = false;
  rejectPatterns = [];
}

async function main() {

  // ============ [1] 左列表口径：只列本次勾选进错题本的题 ============
  console.log('\n[1] 左列表只列「本次收录」的题');
  reset();
  sandbox.openMistakeReview();
  await flush();
  const listHtml = String(elementFor('mrList').innerHTML || '');
  check('列表含已勾选的 #11 与 #13', listHtml.indexOf('selectMistakeReviewRecord(11)') >= 0 && listHtml.indexOf('selectMistakeReviewRecord(13)') >= 0);
  check('列表不含未勾选的 #12', listHtml.indexOf('selectMistakeReviewRecord(12)') < 0, listHtml.slice(0, 200));
  check('面板标题写明本次收录 3 道', textOf('mrMeta').indexOf('本次收录 3 道') >= 0, textOf('mrMeta'));
  check('打开即选中第一道并填充题面', elementFor('mrContent').value === BASE_RECORDS[0].content);

  // ============ [2] 状态角标 ============
  console.log('\n[2] 状态角标：未识别 / 已就绪 / 已入库');
  check('题面为空 → 标「未识别」', listHtml.indexOf('未识别') >= 0);
  check('识别完成有题面 → 标「已就绪」', listHtml.indexOf('已就绪') >= 0);
  check('已入库 → 标「已入库」', listHtml.indexOf('已入库') >= 0);

  // ============ [3] 门禁：题面为空不许生成 ============
  console.log('\n[3] 门禁拦截（核心）');
  sandbox.generateReviewHandout();
  await flush();
  check('题面为空时【不发出】入库请求', importPosts().length === 0, JSON.stringify(importPosts().map(function (r) { return r.url; })));
  check('门禁提示条出现且点名是哪几道', !elementFor('mrGateBar').classList.contains('hidden') && textOf('mrGateText').indexOf('题面还是空的') >= 0, textOf('mrGateText'));
  check('toast 说明原因', textOf('toastMessage').indexOf('题面为空') >= 0, textOf('toastMessage'));
  check('识别按钮文案更新为「识别未完成的 1 道」', String(recBtnLabel.textContent || '').indexOf('识别未完成的 1 道') >= 0, recBtnLabel.textContent);
  check('识别按钮保持可见', !elementFor('mrRecognizeBtn').classList.contains('hidden'));

  // ============ [4] 放行：record_ids 恰等于本次收录 ============
  console.log('\n[4] 放行时入库口径（核心）');
  reset(BASE_RECORDS.map(function (r) {
    return r.include_in_handout ? Object.assign({}, r, { content: '有题面 ' + r.id }) : Object.assign({}, r);
  }));
  sandbox.openMistakeReview();
  await flush();
  requests.length = 0;
  sandbox.generateReviewHandout();
  await flush();
  const ids = lastImportIds();
  check('恰好发出一次入库请求', importPosts().length === 1, String(importPosts().length));
  check('请求打在本批次上', /\/api\/mistakes\/batches\/7\/import-to-bank$/.test(importPosts()[0].url), importPosts()[0].url);
  check('record_ids 恰为本次收录的 [11,13,14]',
    ids && ids.slice().sort().join(',') === '11,13,14', JSON.stringify(ids));
  check('未勾选的 12 没被顺带灌进去', ids && ids.indexOf(12) < 0, JSON.stringify(ids));
  check('入库题被送进组卷试题篮', cartCalls.length === 1 && cartCalls[0].map(function (p) { return p.id; }).sort().join(',') === '1011,1013,1014', JSON.stringify(cartCalls));
  check('解答题给 12 分的默认分值', cartCalls[0] && cartCalls[0][0].score === 12, JSON.stringify(cartCalls[0]));
  check('随后切到组卷工作台', workspaceCalls.length === 1 && workspaceCalls[0][0] === 'paper', JSON.stringify(workspaceCalls));
  check('生成后退出审校页', elementFor('mistakeReviewView').classList.contains('hidden'));

  // ============ [5] 编辑：防抖静默保存的字段名 ============
  console.log('\n[5] 编辑 LaTeX 后防抖保存');
  reset(BASE_RECORDS.map(function (r) {
    return r.include_in_handout ? Object.assign({}, r, { content: '有题面 ' + r.id }) : Object.assign({}, r);
  }));
  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(13);
  check('切题后题面/解析进 textarea',
    elementFor('mrContent').value === '有题面 13' && elementFor('mrAnswer').value === '');
  requests.length = 0;
  elementFor('mrContent').value = '改过的题面 $x^2$';
  elementFor('mrAnswer').value = '解析步骤';
  sandbox.scheduleMistakeReviewSave();
  check('防抖期内还没发请求', recordPuts().length === 0);
  await flush();
  check('防抖到期后发出 PUT', recordPuts().length === 1, String(recordPuts().length));
  const putBody = JSON.parse(recordPuts()[0].options.body || '{}');
  check('PUT 打在选中的那条记录上', /\/api\/mistakes\/records\/13$/.test(recordPuts()[0].url), recordPuts()[0].url);
  check('题面走 content 字段', putBody.content === '改过的题面 $x^2$', JSON.stringify(putBody));
  check('解析走 answer_markdown 字段', putBody.answer_markdown === '解析步骤', JSON.stringify(putBody));
  check('本地状态同步更新', String(store.records.filter(function (r) { return r.id === 13; })[0].content).indexOf('改过的题面') >= 0);
  check('保存态提示写「已保存」', String(elementFor('mrSaveState').textContent || '').indexOf('已保存') >= 0, elementFor('mrSaveState').textContent);

  // 未改动时不该空跑一次 PUT（模板里 oninput 会在任何输入时触发）
  requests.length = 0;
  sandbox.scheduleMistakeReviewSave();
  await flush();
  check('内容没变则不空跑 PUT', recordPuts().length === 0, String(recordPuts().length));

  // ============ [6] 补跑识别：只带缺的那几道 ============
  console.log('\n[6] 补跑识别只带缺的题');
  reset();
  sandbox.openMistakeReview();
  await flush();
  requests.length = 0;
  sandbox.recognizeMissingForReview();
  await flush();
  check('发出一次识别请求', recognizePosts().length === 1, String(recognizePosts().length));
  const recogBody = JSON.parse(recognizePosts()[0].options.body || '{}');
  check('record_ids 恰为缺识别的那道 [11]', JSON.stringify(recogBody.record_ids) === '[11]', JSON.stringify(recogBody.record_ids));
  check('识别也带上当前引擎设置', 'engine' in recogBody);

  // ============ [7] 返回 ============
  console.log('\n[7] 返回批次详情');
  reset();
  sandbox.openMistakeReview();
  await flush();
  check('审校页打开时详情页隐藏', elementFor('mistakeDetailView').classList.contains('hidden'));
  sandbox.closeMistakeReview();
  await flush();
  check('返回后审校页隐藏', elementFor('mistakeReviewView').classList.contains('hidden'));
  check('返回后详情页恢复', !elementFor('mistakeDetailView').classList.contains('hidden'));

  // ============ [8] 静态护栏：模板里 onclick 引用的函数必须已导出 ============
  // 审校区的 HTML 全在 index.html，这里只能钉住 JS 侧出口存在；
  // 少一个 window.x = 就是「点了没反应」，且不报错。
  console.log('\n[8] 模板 onclick 引用的入口已导出');
  ['openMistakeReview', 'closeMistakeReview', 'selectMistakeReviewRecord',
    'scheduleMistakeReviewSave', 'recognizeMissingForReview', 'generateReviewHandout',
    'removeMistakeReviewRecord'
  ].forEach(function (name) {
    check('window.' + name + ' 可调用', typeof sandbox[name] === 'function');
  });


  // ============ [9] 逐题识别：出结果就画出来，但不抢界面、不冲掉在敲的字 ============
  // 这一节钉的是本轮新增的整条链：后端逐题落库 → 任务带队列 → 前端每次轮询重绘，
  // 同时守住两条边界（不切当前选中的题 / 不回填正在编辑的输入框）。
  console.log('\n[9] 逐题识别流式呈现');

  reset();
  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(11);
  await flush();
  const visitedDoc = String(elementFor('mrDocLabel').textContent || '');

  // 服务端已经落库了第 1 道；第 2、3 道还没跑。
  serverRecords = BASE_RECORDS.map(function (r) {
    if (r.id === 11) return Object.assign({}, r, { content: '第 1 道题面', recognize_status: 'done' });
    return Object.assign({}, r);
  });
  // 第一次轮询刻意不带队列字段：这时界面上只能靠前端自己点在起点亮队列。
  taskScript = [
    { status: 'running', progress: 5, steps: [], log: '排队中…' },
    { status: 'running', progress: 30, steps: [], log: '正在识别第 1/2 题…',
      recognize_queue: [13, 14], recognize_done: [], recognize_current: 13 },
    { status: 'running', progress: 70, steps: [], log: '正在识别第 2/2 题…',
      recognize_queue: [13, 14], recognize_done: [13], recognize_current: null },
    { status: 'running', progress: 95, steps: [], log: '收尾…',
      recognize_queue: [13, 14], recognize_done: [13, 14], recognize_current: null }
  ];
  sandbox.recognizeOneMistake(13);
  await flush();
  check('任务创建后立刻点亮队列（不等服务端下发）',
    store.recognizeLive.active === true && store.recognizeLive.queue.join(',') === '13',
    JSON.stringify(store.recognizeLive));
  check('队列里的题标「排队中」',
    String(elementFor('mrList').innerHTML || '').indexOf('排队中') >= 0,
    String(elementFor('mrList').innerHTML || '').slice(0, 240));

  // 第二次轮询：13 在跑、14 排队
  runIntervals();
  await flush();
  const liveList = String(elementFor('mrList').innerHTML || '');
  check('正在识别的那道标「识别中」', liveList.indexOf('识别中') >= 0, liveList.slice(0, 300));
  check('还排在队列里的仍标「排队中」', liveList.indexOf('排队中') >= 0);
  check('识别按钮改报进度并禁用',
    String(recBtnLabel.textContent || '').indexOf('识别中 1/2') >= 0 &&
    elementFor('mrRecognizeBtn').disabled === true,
    String(recBtnLabel.textContent) + ' / disabled=' + elementFor('mrRecognizeBtn').disabled);
  // 核心：界面必须停在用户点开的那道，不许被识别完成的题抢过去
  check('识别中不切换当前选中的题',
    String(elementFor('mrDocLabel').textContent || '') === visitedDoc,
    '期望 ' + visitedDoc + ' 实得 ' + String(elementFor('mrDocLabel').textContent || ''));
  check('未点开的题不会被自动填充进编辑区',
    elementFor('mrContent').disabled === false && elementFor('mrContent').value === '第 1 道题面',
    elementFor('mrContent').value);

  // 用户点开正在识别的那道：编辑区必须只读
  sandbox.selectMistakeReviewRecord(13);
  await flush();
  check('正在识别的题编辑区只读',
    elementFor('mrContent').disabled === true && elementFor('mrAnswer').disabled === true);
  check('只读提示条出现', !elementFor('mrLockHint').classList.contains('hidden'));
  check('提示条说明是「正在识别」',
    textOf('mrLockHintText').indexOf('正在识别') >= 0, textOf('mrLockHintText') || '(空)');

  // 服务端把 13 落库了 —— 界面就停在这一道，应当自动回填并解锁
  serverRecords = BASE_RECORDS.map(function (r) {
    if (r.id === 11) return Object.assign({}, r, { content: '第 1 道题面', recognize_status: 'done' });
    if (r.id === 13) return Object.assign({}, r, { content: '第 2 道题面', recognize_status: 'done' });
    return Object.assign({}, r);
  });
  runIntervals();
  await flush();
  check('识别完成后该题自动解锁', elementFor('mrContent').disabled === false);
  check('只读提示条收起', elementFor('mrLockHint').classList.contains('hidden'));
  check('识别结果自动落到编辑区',
    elementFor('mrContent').value === '第 2 道题面', elementFor('mrContent').value);
  check('出结果后不再标「识别中」',
    String(elementFor('mrList').innerHTML || '').indexOf('识别中') < 0,
    String(elementFor('mrList').innerHTML || '').slice(0, 240));

  // 用户开始打字后，后续轮询不许把服务端内容倒进来覆盖
  elementFor('mrContent').value = '我手动改的题面';
  serverRecords = BASE_RECORDS.map(function (r) {
    if (r.id === 13) return Object.assign({}, r, { content: '服务端又变了一次', recognize_status: 'done' });
    return Object.assign({}, r);
  });
  runIntervals();
  await flush();
  check('有未保存改动时不回填，保住用户输入',
    elementFor('mrContent').value === '我手动改的题面', elementFor('mrContent').value);
  requests.length = 0;
  sandbox.scheduleMistakeReviewSave();
  await flush();
  check('解锁后用户的改动照常保存',
    recordPuts().length === 1 &&
    JSON.parse(recordPuts()[0].options.body || '{}').content === '我手动改的题面',
    String(recordPuts().length));

  // ============ [9b] 识别出的教材定位要自己落到分类下拉 ============
  // 后端现在会在识别时一并判学段/章节/小节（照学科传教材树，见
  // get_subject_curriculum_tree）。落库之后下拉得跟着切过去 —— 否则用户点完
  // 「重新识别这道」，学段那里还写着「选择学段」，看着像又没认出来。
  console.log('\n[9b] 识别出的教材定位落到分类下拉');

  reset();
  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(13);
  await flush();
  check('起点：识别前学段为空', elementFor('mrCompulsory').value === '',
    elementFor('mrCompulsory').value);

  // 拍子要数准：pollTask 起手就 tick() 一次，之后每秒一拍。所以「起任务」本身已经用掉
  // 一拍（13 在跑 → 锁上），服务端落库要插在**下一拍之前**，否则那一拍看到的还是旧快照。
  taskScript = [
    { status: 'running', progress: 30, steps: [], log: '正在识别…',
      recognize_queue: [13], recognize_done: [], recognize_current: 13 },
    { status: 'running', progress: 95, steps: [], log: '收尾…',
      recognize_queue: [13], recognize_done: [13], recognize_current: null }
  ];
  sandbox.recognizeOneMistake(13);
  await flush();
  check('识别中的题编辑区只读', elementFor('mrContent').disabled === true);
  serverRecords = BASE_RECORDS.map(function (r) {
    if (r.id !== 13) return Object.assign({}, r);
    return Object.assign({}, r, {
      content: '识别出来的题面',
      recognize_status: 'done',
      category_compulsory: '必修第一册',
      category_chapter: '第一章 运动的描述',
      category_knowledge: '质点 参考系'
    });
  });
  runIntervals();          // 下一拍：13 已落库 → 解锁 + 回填题面 + 回填分类
  await flush();
  check('识别出的学段自动落到下拉', elementFor('mrCompulsory').value === '必修第一册',
    elementFor('mrCompulsory').value);
  check('章节跟着落', elementFor('mrChapter').value === '第一章 运动的描述',
    elementFor('mrChapter').value);
  check('小节跟着落', elementFor('mrKnowledge').value === '质点 参考系',
    elementFor('mrKnowledge').value);

  // 反向边界：用户手选之后、保存还在途中（PUT 挂着、服务端还是旧值）时，
  // 轮询不许把他刚选的值抹掉。判据必须是「服务端记录 vs 上次回填的快照」——
  // 拿「服务端 vs 下拉当前值」比，这一条会红。轮询刻意跑在**另一道题**（14）上，
  // 否则 13 被锁成只读，refreshReviewLive 提前 return，这条就成了假绿。
  deferPuts = true;
  const beforeSave = serverRecords.map(function (r) { return Object.assign({}, r); });
  taskScript = [
    { status: 'running', progress: 40, steps: [], log: '正在识别…',
      recognize_queue: [14], recognize_done: [], recognize_current: 14 },
    { status: 'running', progress: 80, steps: [], log: '正在识别…',
      recognize_queue: [14], recognize_done: [], recognize_current: 14 },
    { status: 'running', progress: 95, steps: [], log: '收尾…',
      recognize_queue: [14], recognize_done: [14], recognize_current: null }
  ];
  sandbox.recognizeOneMistake(14);
  await flush();
  elementFor('mrChapter').value = '第二章 匀变速直线运动';
  sandbox.onReviewChapterChange();
  await flush();           // 防抖到点 → PUT 发出但挂在途中
  serverRecords = beforeSave;   // 服务端其实还没收到这次保存
  runIntervals();
  await flush();
  check('手选后保存未往返时，轮询不抹掉用户刚选的章节',
    elementFor('mrChapter').value === '第二章 匀变速直线运动', elementFor('mrChapter').value);
  runIntervals();
  await flush();
  check('再拍一次也不抹', elementFor('mrChapter').value === '第二章 匀变速直线运动',
    elementFor('mrChapter').value);
  deferPuts = false;
  releasePuts();
  await flush();

  // ============ [10] 识别进行中的两道门禁 ============
  console.log('\n[10] 识别进行中：不许入库、不许写回手写内容');
  reset();
  // serverRecords 必须一起设：识别还在跑时 openMistakeReview 会补拉一次详情，那一拉
  // 用服务端快照整个覆盖 state.records。只设 store.records 会被抹掉，于是「题面为空」
  // 的门禁先一步拦下 —— 这条用例就变成了假绿，测的根本不是识别门禁。
  serverRecords = BASE_RECORDS.map(function (r) {
    return r.include_in_handout ? Object.assign({}, r, { content: '有题面 ' + r.id }) : Object.assign({}, r);
  });
  store.records = serverRecords.map(function (r) { return Object.assign({}, r); });
  store.recognizeLive.active = true;
  store.recognizeLive.queue = [13];
  store.recognizeLive.done = [];
  store.recognizeLive.current = 13;
  sandbox.openMistakeReview();
  await flush();
  requests.length = 0;
  elementFor('toastMessage').textContent = '';
  sandbox.generateReviewHandout();
  await flush();
  // 识别还没跑完就入库，后续落库的识别结果会改写已经入库的题，题库那份与错题库对不上。
  check('识别进行中拦下入库', importPosts().length === 0, String(importPosts().length));
  check('提示说明为什么不让入库',
    textOf('toastMessage').indexOf('识别还在进行中') >= 0, textOf('toastMessage') || '(空)');

  sandbox.selectMistakeReviewRecord(13);
  await flush();
  check('正在识别的那道被锁成只读', elementFor('mrContent').disabled === true);
  elementFor('mrContent').value = '识别期间手写的';
  requests.length = 0;
  sandbox.scheduleMistakeReviewSave();
  await flush();
  // 写回只会被随后的识别结果冲掉，等于白改一次 —— 干脆不发。
  check('识别期不把手写内容写回服务端', recordPuts().length === 0, String(recordPuts().length));

  // ============ [11] 识别在别处起头，进审校页后接力 ============
  console.log('\n[11] 审校页隐藏时起的识别，进来后接力刷新');
  reset();
  elementFor('mistakeReviewView').classList.add('hidden');
  taskScript = [{ status: 'running', progress: 40, steps: [], log: '正在识别…',
    recognize_queue: [11, 13], recognize_done: [], recognize_current: 11 }];
  sandbox.recognizeMissingForReview();
  await flush();
  check('审校页不可见时不空跑详情请求', detailGets().length === 0, String(detailGets().length));
  requests.length = 0;
  elementFor('mistakeReviewView').classList.remove('hidden');
  sandbox.openMistakeReview();
  await flush();
  check('进审校页后补一次进度同步', detailGets().length >= 1, String(detailGets().length));
  check('进页面即按队列上锁',
    store.recognizeLive.active === true && store.recognizeLive.current === 11 &&
    elementFor('mrContent').disabled === true,
    JSON.stringify(store.recognizeLive) + ' / disabled=' + elementFor('mrContent').disabled);


  // ============ [12] 题卡只管选题；进审校页自动识别 ============
  console.log('\n[12] 题卡不摆编辑类按钮 + 进审校页自动识别');
  reset();
  // 卡片由 renderCards 写进 mistakeCardList；setMistakeCardFilter 是最省事的触发口
  // （无构建前端里 renderCards 是模块私有函数，没挂 window）。
  sandbox.setMistakeCardFilter('all');
  await flush();
  const cardHtml = String(elementFor('mistakeCardList').innerHTML || '');
  check('题卡不再有「编辑题面」（改题面归审校页）',
    cardHtml.indexOf('editMistakeContent(') < 0, cardHtml.slice(0, 200));
  check('题卡不再有「重新识别」（重试归审校页）',
    cardHtml.indexOf('recognizeOneMistake(') < 0);
  check('题卡不再有「上传图形 / 上传解析截图」',
    cardHtml.indexOf('uploadMistakeAsset(') < 0);
  check('题卡不再有「入库」（不再绕过审校门禁）',
    cardHtml.indexOf('importOneMistake(') < 0);
  check('题卡仍保留选题要用的：对错判定 / 题型 / 收录勾选',
    cardHtml.indexOf('toggleMistakeGrad(') >= 0 &&
    cardHtml.indexOf('setMistakeRecordType(') >= 0 &&
    cardHtml.indexOf('setMistakeInclude(') >= 0,
    cardHtml.slice(0, 200));
  check('审校页的「重新识别这道」「原卷框选补图」入口已导出',
    typeof sandbox.recognizeCurrentReviewRecord === 'function' &&
    typeof sandbox.openMistakeCropModal === 'function');

  // 进审校页自动识别：BASE 里只有 #11 是「勾选了、题面为空、还没跑过」
  reset();
  sandbox.openMistakeReview();
  await flush();
  const autoIds = lastRecognizeIds();
  check('进审校页自动发起识别', recognizePosts().length === 1, String(recognizePosts().length));
  check('自动识别只带缺题面的 #11，不碰已就绪的 #13 / 已入库的 #14',
    Array.isArray(autoIds) && autoIds.length === 1 && autoIds[0] === 11,
    JSON.stringify(autoIds));

  // 上次失败的题不自动重跑：那多半是引擎 / 配置问题，自动重跑只会反复烧额度
  reset(BASE_RECORDS.map(function (r) {
    return r.id === 11 ? Object.assign({}, r, { recognize_status: 'failed' }) : Object.assign({}, r);
  }));
  sandbox.openMistakeReview();
  await flush();
  check('上次识别失败的题不自动重跑', recognizePosts().length === 0,
    String(recognizePosts().length));

  // 已经在跑时不能再起一个：两个任务抢同一批记录，落库顺序就乱了
  reset();
  store.recognizeLive.active = true;
  store.recognizeLive.queue = [13];
  store.recognizeLive.current = 13;
  sandbox.openMistakeReview();
  await flush();
  check('识别进行中不重复发起任务', recognizePosts().length === 0,
    String(recognizePosts().length));

  // 失败原因要浮到审校页：任务条在批次详情页头部，人站在审校页看不到它，
  // 屏幕上只剩一句「成功 0 道，失败 2 道」，等于没给原因。
  reset();
  store.recognizeLive.errors = ['第 1 题（记录 104）：题块图不存在：/static/x.png'];
  sandbox.openMistakeReview();
  await flush();
  check('识别失败原因显示在审校页提示条',
    textOf('mrGateText').indexOf('题块图不存在') >= 0, textOf('mrGateText') || '(空)');

  // 题目标签口径：题号缺失时不能再双双显示成「第 1 页」，得能区分是哪一块
  check('题目标签带页号 + 块号',
    String(elementFor('mrList').innerHTML || '').indexOf('第 1 页 块') >= 0,
    String(elementFor('mrList').innerHTML || '').slice(0, 200));


  // ==================================================== [13] 原卷框选补图
  // 需求（2026-09-15）：①在原卷页面上框选，不从磁盘挑图；②图写进**正文**，落在光标处
  // 或占位符处，不再统一追加到文字尾部；③「[插图待补: 图N]」在预览里是可点芯片，
  // 点一下就去补；④框选不再显示像素尺寸。
  detailPages = [
    { page_no: 1, url: '/page_1.png?v=1', width: 1000, height: 2000, blocks: [],
      layout: { mode: 'single', boundary: 0.5, source: 'text_layer', confidence: 1.0 },
      column_boundary: 0.5, snap_points: [], gap_candidates: [],
      column_snap_points: {}, column_gap_candidates: {},
      manual_boxes: [], manual_merges: [], hidden_blocks: [] },
    { page_no: 2, url: '/page_2.png?v=1', width: 1000, height: 2000, blocks: [],
      layout: { mode: 'single', boundary: 0.5, source: 'text_layer', confidence: 1.0 },
      column_boundary: 0.5, snap_points: [], gap_candidates: [],
      column_snap_points: {}, column_gap_candidates: {},
      manual_boxes: [], manual_merges: [], hidden_blocks: [] }
  ];
  const CROP_URL = '/static/uploads/mistakes/7/figures/crop_test.png';
  reset([
    // 21：有占位符、数组里没有配对图 → 芯片
    rec(21, {
      page_no: 2, question_no: '5', include_in_handout: true, recognize_status: 'done',
      content: '如图，求 $x$。[插图待补: 图1]', figure_images: []
    }),
    // 22：没有占位符 —— 靠自己把光标点好再补
    rec(22, {
      page_no: 2, question_no: '6', include_in_handout: true, recognize_status: 'done',
      content: '第二题，没有占位符。', figure_images: []
    }),
    // 23：两个占位符 + 一张老配图 → 第 1 个占位符直接出图，第 2 个才是芯片
    rec(23, {
      page_no: 1, question_no: '7', include_in_handout: true, recognize_status: 'done',
      content: '第三题 [插图待补: 图1] 与 [插图待补: 图2]。',
      figure_images: ['/static/uploads/mistakes/7/figures/keep.png']
    })
  ]);
  cropCalls.length = 0;

  // 假 DOM 没有布局系统：把页图的显示尺寸钉成 400×566，坐标换算才可预期。
  const cropImg = elementFor('mrCropImage');
  cropImg.naturalWidth = 1000;
  cropImg.naturalHeight = 2000;
  cropImg.clientWidth = 400;
  cropImg.clientHeight = 566;
  cropImg.complete = true;
  cropImg.getBoundingClientRect = function () {
    return { left: 0, top: 0, width: 400, height: 566, right: 400, bottom: 566 };
  };
  elementFor('mrCropWrapper').clientWidth = 640;
  elementFor('mrCropWrapper').clientHeight = 480;

  const cropBox = elementFor('mrCropImageBox');
  const dragOn = function (x1, y1, x2, y2) {
    // 刻意不再伪造 event.target：四角圆点删掉后（2026-09-15），mousedown 不读任何手柄
    // 元素，缩放全靠 mrCropHitHandle 的坐标判定。留着假 target 反而会掩盖
    // 「又去读 DOM 手柄」这类回归。
    cropBox.handlers.mousedown[0]({
      clientX: x1, clientY: y1,
      preventDefault: function () {}
    });
    fireDocument('mousemove', { clientX: x2, clientY: y2, preventDefault: function () {} });
    fireDocument('mouseup', {});
  };
  // 用户把光标点到某个位置（真实浏览器里是 focus/click/键盘，夹具直接触发同一批监听）。
  const placeCursor = function (id, pos) {
    const area = elementFor(id);
    area.selectionStart = pos;
    area.selectionEnd = pos;
    (area.handlers.focus || []).forEach(function (fn) { fn({ target: area }); });
  };
  const lastPut = function () {
    const puts = recordPuts();
    return puts.length ? JSON.parse(puts[puts.length - 1].options.body || '{}') : {};
  };

  check('框选不再显示像素尺寸标识（模板与脚本里都没有 mrCropSizeTag）',
    src.indexOf('mrCropSizeTag') < 0, '脚本里还留着尺寸标签的引用');

  elementFor('mrCropModal').classList.add('hidden');
  sandbox.closeMistakeReview();
  sandbox.openMistakeCropModal('figure_images');
  check('没选中题目时不开弹窗，只提示先选题',
    elementFor('mrCropModal').classList.contains('hidden') === true &&
    textOf('toastMessage').indexOf('先在左侧选一道题') >= 0,
    textOf('toastMessage'));

  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(21);
  await flush();

  const chipHtml = String(elementFor('mrPreview').innerHTML || '');
  check('未补的占位符在预览里渲染成标记（2026-09-18 起不可点）',
    chipHtml.indexOf('［插图待补：图1］') >= 0 &&
    chipHtml.indexOf('openMistakeCropModalFromPlaceholder') < 0,
    chipHtml.slice(0, 280));
  check('没配图时不会凭空长出图片', chipHtml.indexOf('<img') < 0, chipHtml.slice(0, 200));

  // ---- 工具条入口：没放过光标 → 落正文末尾 ----
  requests.length = 0;
  sandbox.openMistakeCropModal('figure_images');
  cropImg.onload();
  await flush();

  check('「补图形」打开的是原卷框选弹窗（不再弹文件选择器）',
    !elementFor('mrCropModal').classList.contains('hidden'));
  check('弹窗自动跳到本题所在页（本题在第 2 页）',
    textOf('mrCropPageIndicator').indexOf('第 2 / 2 页') >= 0, textOf('mrCropPageIndicator'));
  check('载入的页图就是本题那一页',
    cropImg.getAttribute('data-loaded') === '/page_2.png?v=1',
    String(cropImg.getAttribute('data-loaded')));
  check('左侧缩略图标出哪一页是本题',
    textOf('mrCropThumbs').indexOf('· 本题') >= 0, textOf('mrCropThumbs').slice(0, 160));
  check('目标徽标写明补入位置是本题配图',
    textOf('mrCropTargetBadgeText').indexOf('本题配图') >= 0, textOf('mrCropTargetBadgeText'));
  check('「本题范围」虚线参考框按本题块坐标画出',
    !elementFor('mrCropBlockRect').classList.contains('hidden') &&
    String(elementFor('mrCropBlockRect').style.width || '').indexOf('px') >= 0,
    JSON.stringify(elementFor('mrCropBlockRect').style));
  check('还没框选时确认按钮是禁用的', elementFor('mrCropConfirmBtn').disabled === true);
  check('从工具条进来时提示「插到光标停的位置」',
    textOf('mrCropHintText').indexOf('插到你光标停的位置') >= 0, textOf('mrCropHintText'));

  dragOn(40, 60, 240, 140);
  await flush();
  check('拖拽框选后确认按钮才可用', elementFor('mrCropConfirmBtn').disabled === false);

  // ---- 四角圆点删掉后，缩放改由坐标命中；顺序必须「先判角、再判框内」（2026-09-15）----
  const rectNow = function () {
    const s = elementFor('mrCropSelectRect').style;
    return [s.left, s.top, s.width, s.height].join(',');
  };
  check('拖出来的是线条框，尺寸落在 rect 自己身上', rectNow() === '40px,60px,200px,80px', rectNow());

  // 当前框 nw(40,60) ne(240,60) sw(40,140) se(240,140)。压住 se 角往外拖 ——
  // se 角同时也在框内，所以这一条顺带证明「角优先于整框平移」。
  dragOn(240, 140, 280, 180);
  check('按住角拖动＝缩放（锚定对角 nw），不是整框平移',
    rectNow() === '40px,60px,240px,120px', rectNow());

  // 框中心 (160,120) 离四角都远 → 整框平移，宽高不变
  dragOn(160, 120, 180, 130);
  check('按住框内部拖动＝整框平移（宽高不变）',
    rectNow() === '60px,70px,240px,120px', rectNow());

  // 上面的缩放/平移已经改掉了选区，下面要验「归一化坐标」是按 40,60→240,140 算的，
  // 所以先重画回那个框（起点在框外 → 走 draw）。
  dragOn(40, 60, 240, 140);
  check('重画回原始框，供归一化坐标断言使用', rectNow() === '40px,60px,200px,80px', rectNow());

  sandbox.submitMrCrop();
  await flush();

  const cropPost = cropCalls[cropCalls.length - 1];
  const near = function (a, b) { return Math.abs(a - b) < 0.01; };
  check('裁图请求打到本页的 crop-figure 端点',
    !!cropPost && /\/api\/mistakes\/batches\/7\/pages\/2\/crop-figure$/.test(cropPost.url),
    cropPost ? cropPost.url : '(没有请求)');
  check('框选坐标按页面尺寸归一化成 0–1（与切块同一套）',
    !!cropPost && near(cropPost.body.xmin, 0.1) && near(cropPost.body.xmax, 0.6) &&
    near(cropPost.body.ymin, 60 / 566) && near(cropPost.body.ymax, 140 / 566),
    cropPost ? JSON.stringify(cropPost.body) : '(没有请求)');

  let put = lastPut();
  check('没放过光标时插图落正文末尾，不会跑到题面最前面',
    String(put.content || '').indexOf('如图，求 $x$。[插图待补: 图1]\n\n![插图](' + CROP_URL + ')') === 0,
    JSON.stringify(put));
  check('图片不再靠 figure_images 数组定位（数组没被改写）',
    put.figure_images === undefined, JSON.stringify(put));
  check('补完自动关闭弹窗', elementFor('mrCropModal').classList.contains('hidden') === true);

  let previewHtml = String(elementFor('mrPreview').innerHTML || '');
  check('预览把这张内联图渲染出来了',
    previewHtml.indexOf(CROP_URL) >= 0, previewHtml.slice(0, 240));
  check('预览里的内联图带「移除」入口（补错了能撤）',
    previewHtml.indexOf("removeInlineMistakeImage('figure_images',0)") >= 0,
    previewHtml.slice(0, 300));

  // ---- 移除内联图 ----
  requests.length = 0;
  sandbox.removeInlineMistakeImage('figure_images', 0);
  await flush();
  put = lastPut();
  check('移除内联图 = 从正文里摘掉那段 markdown（占位符回到原样）',
    String(put.content || '') === '如图，求 $x$。[插图待补: 图1]', JSON.stringify(put));

  // ---- 源码占位符浮层入口：就地替换那个标记 ----
  requests.length = 0;
  sandbox.openMistakeCropModalFromPlaceholder('figure_images', 0);
  cropImg.onload();
  await flush();
  check('从源码占位符进来时提示「就地替换那个占位符」',
    textOf('mrCropHintText').indexOf('就地换') >= 0, textOf('mrCropHintText'));
  dragOn(40, 60, 240, 140);
  await flush();
  sandbox.submitMrCrop();
  await flush();
  put = lastPut();
  check('确认后占位符被就地换成真图，题面里不再留「插图待补」',
    String(put.content || '').indexOf('![插图](' + CROP_URL + ')') >= 0 &&
    String(put.content || '').indexOf('插图待补') < 0,
    JSON.stringify(put));
  previewHtml = String(elementFor('mrPreview').innerHTML || '');
  check('预览跟着变成真图、芯片消失',
    previewHtml.indexOf(CROP_URL) >= 0 && previewHtml.indexOf('插图待补') < 0,
    previewHtml.slice(0, 240));

  // ---- 光标处插入（无占位符的题）----
  sandbox.selectMistakeReviewRecord(22);
  await flush();
  previewHtml = String(elementFor('mrPreview').innerHTML || '');
  check('没有占位符的题不会凭空长出图或芯片',
    previewHtml.indexOf('<img') < 0 && previewHtml.indexOf('插图待补') < 0,
    previewHtml.slice(0, 200));

  placeCursor('mrContent', 4);   // 「第二题，|没有占位符。」
  requests.length = 0;
  sandbox.openMistakeCropModal('figure_images');
  cropImg.onload();
  await flush();
  dragOn(40, 60, 240, 140);
  await flush();
  sandbox.submitMrCrop();
  await flush();
  put = lastPut();
  const insertedAt = String(put.content || '').indexOf('![插图](');
  check('插图插在光标处，而不是追加到文字尾部',
    insertedAt > 0 && String(put.content || '').indexOf('第二题，') === 0 &&
    String(put.content || '').trim().endsWith('没有占位符。'),
    JSON.stringify(put));
  check('插进去的图两边补了空行（不会跟正文挤在一行）',
    String(put.content || '').indexOf('第二题，\n\n![插图](') === 0, JSON.stringify(put));

  // ---- 老配图仍按出现顺序配对，只有配不到的那个是芯片 ----
  sandbox.selectMistakeReviewRecord(23);
  await flush();
  previewHtml = String(elementFor('mrPreview').innerHTML || '');
  check('数组里有配图的那个占位符直接出图',
    previewHtml.indexOf('/static/uploads/mistakes/7/figures/keep.png') >= 0,
    previewHtml.slice(0, 300));
  check('配不到图的那个在预览里是标记（序号改由源码区承担）',
    previewHtml.indexOf('［插图待补：图2］') >= 0 &&
    previewHtml.indexOf('openMistakeCropModalFromPlaceholder') < 0,
    previewHtml.slice(0, 400));

  // ---- 芯片序号 = 占位符序号：补第 2 个时，第 1 个必须原样留着 ----
  requests.length = 0;
  sandbox.openMistakeCropModalFromPlaceholder('figure_images', 1);
  cropImg.onload();
  await flush();
  dragOn(40, 60, 240, 140);
  await flush();
  sandbox.submitMrCrop();
  await flush();
  put = lastPut();
  check('补第 2 个占位符时只换那一个，第 1 个原样留着',
    String(put.content || '').indexOf('[插图待补: 图1]') >= 0 &&
    String(put.content || '').indexOf('[插图待补: 图2]') < 0 &&
    String(put.content || '').indexOf('![插图](' + CROP_URL + ')') > 0,
    JSON.stringify(put));

  // ---- 清除残留占位符 ----
  // 用 #23：它的两个占位符都还没补（#21 那个在上一步已经补成图了，按钮本就该藏着）。
  sandbox.selectMistakeReviewRecord(23);
  await flush();
  check('有占位符时「清除残留占位符」按钮可见',
    elementFor('mrClearPlaceholderBtn').style.display !== 'none',
    String(elementFor('mrClearPlaceholderBtn').style.display));
  requests.length = 0;
  sandbox.clearMistakePlaceholders();
  await flush();
  put = lastPut();
  check('清除把两个占位符都从题面里摘掉',
    String(put.content || '').indexOf('插图待补') < 0 &&
    String(put.content || '').indexOf('第三题') === 0 &&
    String(put.content || '').indexOf('与') > 0,
    JSON.stringify(put));
  previewHtml = String(elementFor('mrPreview').innerHTML || '');
  check('清完按钮自己收起',
    elementFor('mrClearPlaceholderBtn').style.display === 'none',
    String(elementFor('mrClearPlaceholderBtn').style.display));

  // ---- [13b] 源码区的占位符入口（2026-09-18）----
  // 需求原文：「这里的插图待补的跳转，我不希望是在这个预览的地方，我原本是希望在 latex
  // 源码编辑的位置点击然后跳转到截图处」。textarea 是纯文本控件、放不进可点元素，所以
  // 「点了哪个占位符」只能按光标位反推 —— 序号错了不会报错，只会把图补到别的位置上。
  sandbox.selectMistakeReviewRecord(23);
  await flush();
  const contentArea = elementFor('mrContent');
  const figureAction = elementFor('mrContentFigureAction');
  check('[13b] 换题后源码浮层默认收起',
    figureAction.classList.contains('hidden') === true,
    String(figureAction.innerHTML).slice(0, 120));

  // 显式写死题面：上一节把 #23 的占位符清过，不能依赖服务端快照还剩什么。
  const srcText = '第三题 [插图待补: 图1] 与 [插图待补: 图2]。';
  contentArea.value = srcText;
  const firstToken = srcText.indexOf('[插图待补: 图1]');
  const secondToken = srcText.indexOf('[插图待补: 图2]');

  placeCursor('mrContent', firstToken + 2);
  check('[13b] 光标落在占位符上 → 浮层出现，按钮带第 1 个的序号',
    figureAction.classList.contains('hidden') === false &&
    String(figureAction.innerHTML).indexOf(
      "openMistakeCropModalFromPlaceholder('figure_images',0)") >= 0,
    String(figureAction.innerHTML).slice(0, 220));
  check('[13b] 按钮文案是「补这张图」并带上占位符标签',
    String(figureAction.innerHTML).indexOf('补这张图') >= 0 &&
    String(figureAction.innerHTML).indexOf('图1') >= 0,
    String(figureAction.innerHTML).slice(0, 220));
  check('[13b] 按钮按 mousedown 掐掉焦点转移（否则 blur 先把按钮收走，click 落不下来）',
    String(figureAction.innerHTML).indexOf('onmousedown="event.preventDefault()"') >= 0,
    String(figureAction.innerHTML).slice(0, 220));

  placeCursor('mrContent', secondToken + 2);
  check('[13b] 光标点到第 2 个占位符 → 序号跟着变成 1（不是永远 0）',
    String(figureAction.innerHTML).indexOf(
      "openMistakeCropModalFromPlaceholder('figure_images',1)") >= 0,
    String(figureAction.innerHTML).slice(0, 220));

  placeCursor('mrContent', 1);   // 「第|三题 …」：正文里，不在任何占位符上
  check('[13b] 光标离开占位符 → 浮层收起',
    figureAction.classList.contains('hidden') === true,
    String(figureAction.innerHTML).slice(0, 120));

  // 解析框里的占位符走同一个入口，但目标是解析配图。
  const answerArea = elementFor('mrAnswer');
  const answerAction = elementFor('mrAnswerFigureAction');
  const savedAnswer = String(answerArea.value);
  const answerText = '解析：$x=1$。[插图待补: 图3]';
  answerArea.value = answerText;
  // 别手算下标：`解析：$x=1$。` 比看上去长，算错就点到正文、而不是占位符上了。
  const answerToken = answerText.indexOf('[插图待补: 图3]');

  placeCursor('mrContent', firstToken + 2);   // 先把题面那个叫出来
  check('[13b] 题面浮层在场（给下一步做对照）',
    figureAction.classList.contains('hidden') === false &&
    String(figureAction.innerHTML).indexOf('补这张图') >= 0,
    String(figureAction.innerHTML).slice(0, 120));
  placeCursor('mrAnswer', answerToken + 2);   // 落在解析里那个占位符中间
  check('[13b] 解析框里的占位符 → 目标是 answer_images',
    answerAction.classList.contains('hidden') === false &&
    String(answerAction.innerHTML).indexOf(
      "openMistakeCropModalFromPlaceholder('answer_images',0)") >= 0,
    String(answerAction.innerHTML).slice(0, 220));
  check('[13b] 两个编辑框不会同时挂着浮层（另一侧要收掉）',
    figureAction.classList.contains('hidden') === true,
    String(figureAction.innerHTML).slice(0, 120));

  // blur 后要等一拍再收：Safari 不认 mousedown 的 preventDefault，点按钮那一刻 textarea
  // 仍会失焦，立刻收等于把按钮从指针底下抽走。
  (answerArea.handlers.blur || []).forEach(function (fn) { fn({ target: answerArea }); });
  check('[13b] blur 当拍还没收（留给 click 落地）',
    answerAction.classList.contains('hidden') === false &&
    String(answerAction.innerHTML).indexOf('补这张图') >= 0,
    String(answerAction.innerHTML).slice(0, 120));
  runTimers();
  check('[13b] 定时器到点后浮层收起',
    answerAction.classList.contains('hidden') === true,
    String(answerAction.innerHTML).slice(0, 120));
  answerArea.value = savedAnswer;   // 别把伪造的解析留给后面的用例

  // ---- 「补解析截图」共用同一个弹窗，落点是解析正文 ----
  requests.length = 0;
  sandbox.selectMistakeReviewRecord(22);
  await flush();
  sandbox.openMistakeCropModal('answer_images');
  cropImg.onload();
  await flush();
  check('「补解析截图」共用同一弹窗、切到解析目标',
    textOf('mrCropTargetBadgeText').indexOf('解析截图') >= 0, textOf('mrCropTargetBadgeText'));
  dragOn(40, 60, 200, 120);
  await flush();
  sandbox.submitMrCrop();
  await flush();
  put = lastPut();
  check('解析截图写进解析正文，并标记来源为人工',
    String(put.answer_markdown || '').indexOf('![插图](' + CROP_URL + ')') >= 0 &&
    put.answer_source === 'manual',
    JSON.stringify(put));

  // ============ [14] 中栏分类信息（按学科收字段）+ 右栏题目预览 ============
  console.log('\n[14] 中栏分类信息与右栏题目预览');

  reset(MATH_RECORDS);
  // reset 会把批次重置成物理（沿用上面那节的上下文），这里按用例改成数学
  store.batch.subject = 'math';
  requests.length = 0;
  sandbox.openMistakeReview();
  await flush();

  const catUrls = requests.filter(function (r) { return /^\/api\/categories/.test(r.url); })
    .map(function (r) { return r.url; });
  check('教材树按批次学科请求（不是固定拉数学那棵）',
    catUrls.indexOf('/api/categories?subject=math') >= 0, catUrls.join(' | '));

  // ---- 回填 ----
  check('切题回填来源', elementFor('mrSource').value === '2025 全国甲卷', elementFor('mrSource').value);
  check('切题回填题型', elementFor('mrQType').value === 'single_choice', elementFor('mrQType').value);
  check('切题回填难度', elementFor('mrDifficulty').value === 'easy_error', elementFor('mrDifficulty').value);
  check('切题回填学段', elementFor('mrCompulsory').value === '必修第一册', elementFor('mrCompulsory').value);
  check('切题回填章节', elementFor('mrChapter').value === '第一章 集合与常用逻辑用语', elementFor('mrChapter').value);
  check('切题回填小节', elementFor('mrKnowledge').value === '集合的概念', elementFor('mrKnowledge').value);
  check('知识点按逗号拆成多个芯片',
    textOf('mrKnowledgeTagsChips').indexOf('集合') >= 0 && textOf('mrKnowledgeTagsChips').indexOf('交集') >= 0,
    textOf('mrKnowledgeTagsChips'));
  check('解题方法回填成芯片', textOf('mrSolveMethodTagsChips').indexOf('定义法') >= 0, textOf('mrSolveMethodTagsChips'));
  check('自定义标签回填', elementFor('mrCustomTags').value === '高一, 期中', elementFor('mrCustomTags').value);
  check('关联章节回填成芯片（带移除入口）',
    textOf('mrRelChips').indexOf('集合间的基本关系') >= 0 && textOf('mrRelChips').indexOf('removeReviewRelatedChapter(') >= 0,
    textOf('mrRelChips'));

  // ---- 数学：全套字段都在 ----
  const richIds = ['mrDifficultyWrap', 'mrTagsRow', 'mrCustomTagsRow'];
  check('数学保留全套分类字段（难度/知识点/解题方法/自定义标签）',
    richIds.every(function (id) { return !elementFor(id).classList.contains('hidden'); }),
    richIds.map(function (id) { return id + '=' + elementFor(id).classList.contains('hidden'); }).join(' '));
  check('数学分类信息第一行是三列',
    elementFor('mrMetaRow1').classList.contains('grid-cols-3') &&
    !elementFor('mrMetaRow1').classList.contains('grid-cols-2'));

  // ---- 换题：库里的章节不在教材树上也不许丢 ----
  sandbox.selectMistakeReviewRecord(32);
  await flush();
  const chapterHtml = String(elementFor('mrChapter').innerHTML || '');
  check('候选里没有的历史值被补进下拉（否则一保存就被静默改分类）',
    chapterHtml.indexOf('<option value="第三章 函数">') >= 0 && elementFor('mrChapter').value === '第三章 函数',
    chapterHtml);
  check('同上：小节的历史值也保住',
    String(elementFor('mrKnowledge').innerHTML || '').indexOf('<option value="函数的单调性">') >= 0,
    String(elementFor('mrKnowledge').innerHTML || ''));

  // ---- 标签芯片增删 ----
  requests.length = 0;
  elementFor('mrKnowledgeTagInput').value = '导数';
  sandbox.onReviewMetaTagKey({ key: 'Enter', preventDefault: function () {} }, 'knowledge');
  check('回车把输入的标签变成芯片', textOf('mrKnowledgeTagsChips').indexOf('导数') >= 0, textOf('mrKnowledgeTagsChips'));
  check('提交后输入框自己清空', elementFor('mrKnowledgeTagInput').value === '');
  await flush();
  put = lastPut();
  check('加标签会落库（knowledge_tags 带上了新标签）',
    String((put || {}).knowledge_tags || '').indexOf('导数') >= 0, JSON.stringify(put));
  requests.length = 0;
  sandbox.removeReviewMetaTag('knowledge', 0);
  check('点芯片上的 × 能摘掉它', textOf('mrKnowledgeTagsChips').indexOf('导数') < 0, textOf('mrKnowledgeTagsChips'));
  await flush();
  put = lastPut();
  check('摘标签同样落库（knowledge_tags 回到空）', String((put || {}).knowledge_tags || '') === '', JSON.stringify(put));

  // ---- 物化：收起用不上的标签 ----
  // 切学科前先把这笔改动落定：草稿还脏的时候重进审校页，测的就是「保存往返窗口内
  // 重绘」这件事，而不是这一段要测的「学科决定收哪些字段」。
  store.batch.subject = 'physics';
  requests.length = 0;
  sandbox.openMistakeReview();
  await flush();
  check('物化收起 难度/知识点/解题方法/自定义标签',
    richIds.every(function (id) { return elementFor(id).classList.contains('hidden'); }),
    richIds.map(function (id) { return id + '=' + elementFor(id).classList.contains('hidden'); }).join(' '));
  check('物化第一行改两列（收起难度后不留空洞）',
    elementFor('mrMetaRow1').classList.contains('grid-cols-2') &&
    !elementFor('mrMetaRow1').classList.contains('grid-cols-3'));
  check('物化仍保留 关联章节',
    !elementFor('mrRelChapterBox').classList.contains('hidden'));
  check('物化的教材树换成物理那棵',
    requests.some(function (r) { return r.url === '/api/categories?subject=physics'; }),
    requests.filter(function (r) { return /^\/api\/categories/.test(r.url); }).map(function (r) { return r.url; }).join(' | '));

  // ---- 关联章节三级级联 + 去重 ----
  elementFor('mrRelCompulsory').value = '必修第一册';
  sandbox.onReviewRelCompulsoryChange();
  check('关联章节：选中学段后章节候选按物理树填充',
    String(elementFor('mrRelChapter').innerHTML || '').indexOf('第一章 运动的描述') >= 0,
    String(elementFor('mrRelChapter').innerHTML || ''));
  elementFor('mrRelChapter').value = '第一章 运动的描述';
  sandbox.onReviewRelChapterChange();
  check('关联章节：选中章节后「添加」按钮才可用', elementFor('mrAddRelChapterBtn').disabled === false);
  sandbox.addReviewRelatedChapter();
  const chipCount = function () {
    return (textOf('mrRelChips').match(/removeReviewRelatedChapter\(/g) || []).length;
  };
  check('添加后出现一条关联章节芯片', chipCount() === 1, textOf('mrRelChips'));
  check('添加后清空三级下拉，避免连点重复添加',
    elementFor('mrRelChapter').value === '' && elementFor('mrAddRelChapterBtn').disabled === true);
  elementFor('mrRelCompulsory').value = '必修第一册';
  sandbox.onReviewRelCompulsoryChange();
  elementFor('mrRelChapter').value = '第一章 运动的描述';
  sandbox.onReviewRelChapterChange();
  sandbox.addReviewRelatedChapter();
  check('重复添加同一条会被挡下', chipCount() === 1, textOf('mrRelChips') + ' / ' + textOf('toastMessage'));
  sandbox.removeReviewRelatedChapter(0);
  check('移除按钮能摘掉关联章节', chipCount() === 0, textOf('mrRelChips'));

  // ---- 保存：只发改动过的字段，题面与分类同一次 PUT ----
  requests.length = 0;
  sandbox.scheduleMistakeReviewSave();
  await flush();
  check('没有改动就不发空请求', recordPuts().length === 0, String(recordPuts().length));

  requests.length = 0;
  elementFor('mrSource').value = '2026 成都一诊';
  sandbox.scheduleMistakeReviewSave();
  await flush();
  let body = JSON.parse(recordPuts()[recordPuts().length - 1].options.body || '{}');
  check('只发改过的那个字段（其余不重复写一遍）',
    body.source === '2026 成都一诊' && body.content === undefined && body.tags === undefined,
    JSON.stringify(body));

  requests.length = 0;
  elementFor('mrSource').value = '2026 成都二诊';
  elementFor('mrContent').value = String(elementFor('mrContent').value) + ' 再补一句。';
  sandbox.scheduleMistakeReviewSave();
  await flush();
  body = JSON.parse(recordPuts()[recordPuts().length - 1].options.body || '{}');
  check('题面与分类在同一次 PUT 里提交', body.content !== undefined && body.source === '2026 成都二诊', JSON.stringify(body));
  check('且只有一个请求（分两笔会互相覆盖）', recordPuts().length === 1, String(recordPuts().length));

  // ---- 右栏：仿真试卷卡片，上题干下解析 ----
  const paper = String(elementFor('mrPreview').innerHTML || '');
  check('右栏是仿真试卷卡片（paper-card）', paper.indexOf('paper-card') >= 0, paper.slice(0, 200));
  check('顶部品牌色条与「本地教研系统」抬头已去掉（用户 2026-09-16 指出多余）',
    paper.indexOf('本地教研系统') < 0 && paper.indexOf('h-2 bg-brand-600') < 0,
    paper.slice(0, 200));
  check('顶部有 编号 / 题型 / 难度 徽章',
    paper.indexOf('编号：') >= 0 && paper.indexOf('题型：') >= 0 && paper.indexOf('难度：') >= 0,
    paper.slice(0, 400));
  check('下半是「参考答案与详细解析」区', paper.indexOf('参考答案与详细解析') >= 0);
  check('题干在上、解析在下（顺序不能倒）',
    paper.indexOf('第六题') < paper.indexOf('参考答案与详细解析'),
    '题干位置=' + paper.indexOf('第六题') + ' 解析区位置=' + paper.indexOf('参考答案与详细解析'));
  check('页脚给了来源', paper.indexOf('来源：') >= 0, paper.slice(-260));

  sandbox.toggleMrPreviewAnswer();
  const folded = String(elementFor('mrPreview').innerHTML || '');
  check('点「隐藏解析」后解析体收起（加 hidden）',
    folded.indexOf('id="mrAnalysisBody"') >= 0 && folded.indexOf('border border-slate-200 leading-relaxed hidden') >= 0,
    folded.slice(-400));
  check('按钮文案变成「显示解析」', folded.indexOf('显示解析') >= 0);
  sandbox.toggleMrPreviewAnswer();
  check('再点一次恢复展开', String(elementFor('mrPreview').innerHTML || '').indexOf('显示解析') < 0);

  sandbox.toggleMrPreviewAnswer();
  sandbox.selectMistakeReviewRecord(31);
  await flush();
  check('换题时解析区恢复展开（上一个的折叠状态不跟过来）',
    String(elementFor('mrPreview').innerHTML || '').indexOf('显示解析') < 0);

  // ============ [15] 解析处 OCR 识别 ============
  console.log('\n[15] 解析截图 OCR');

  // localStorage 默认桩是只读哑的，这里的「记住选择」要真能读回来。
  const lsMap = {};
  sandbox.localStorage = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(lsMap, k) ? lsMap[k] : null; },
    setItem: function (k, v) { lsMap[k] = String(v); },
    removeItem: function (k) { delete lsMap[k]; }
  };
  // 让 ocr.js 的文本清洗可观测：断言走的确实是它，而不是这边另抄的一份。
  sandbox.cleanMathOcrText = function (t) { return String(t).trim() + ' [cleaned]'; };

  reset(MATH_RECORDS);
  store.batch.subject = 'math';
  requests.length = 0;
  await flush();
  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(31);
  await flush();

  check('识别区初始是可上传态',
    !elementFor('mrAnswerOcrIdle').classList.contains('hidden') &&
    elementFor('mrAnswerOcrDone').classList.contains('hidden'));

  // ---- 默认「追加」：不动原有解析 ----
  sandbox.setReviewOcrMode('append');
  elementFor('mrAnswer').value = '第五步，答案是 2。';
  requests.length = 0;
  ocrCalls.length = 0;
  ocrScript = [{ status: 'success', latex: '图像里的解析第一行。' }];
  const ocrFile = { type: 'image/png', __dataUrl: 'data:image/png;base64,THUMB1' };
  sandbox.runReviewAnswerOcr(ocrFile);
  await flush();
  await flush();

  const appended = String(elementFor('mrAnswer').value || '');
  check('追加模式：原有解析留着，识别文本拼在后面',
    appended.indexOf('第五步，答案是 2。') >= 0 &&
    appended.indexOf('[cleaned]') > appended.indexOf('第五步，答案是 2。'),
    appended);
  check('识别文本过的是 ocr.js 那套清洗（出现 [cleaned] 标记）',
    appended.indexOf('图像里的解析第一行。 [cleaned]') >= 0, appended);
  check('识别截图的缩略图上屏（认错图能当场看出来）',
    elementFor('mrAnswerOcrThumb').src === 'data:image/png;base64,THUMB1',
    elementFor('mrAnswerOcrThumb').src);
  check('识别区切到「已识别」态',
    !elementFor('mrAnswerOcrDone').classList.contains('hidden') &&
    elementFor('mrAnswerOcrIdle').classList.contains('hidden'));
  check('发给 /api/ocr 的是这张图 + default 引擎（跟随系统设置）',
    ocrCalls.length === 1 && ocrCalls[0].body.get('file') === ocrFile &&
    ocrCalls[0].body.get('engine') === 'default',
    JSON.stringify(ocrCalls.map(function (c) { return c.body._bag; })));
  check('识别后走题面/解析同一条 PUT 落库（字段 answer_markdown）',
    recordPuts().length === 1 &&
    String(lastPut().answer_markdown || '').indexOf('图像里的解析第一行。') >= 0,
    JSON.stringify({ puts: recordPuts().length, body: lastPut() }));

  // ---- 「替换」：覆盖整段，并且记住选择 ----
  sandbox.setReviewOcrMode('replace');
  check('切到替换写进 localStorage（下次进来还是替换）',
    lsMap['mathbank_review_ocr_mode'] === 'replace', JSON.stringify(lsMap));
  elementFor('mrAnswer').value = '旧的解析，整段都该被覆盖。';
  requests.length = 0;
  ocrCalls.length = 0;
  ocrScript = [{ status: 'success', latex: '覆盖后的解析。' }];
  sandbox.runReviewAnswerOcr({ type: 'image/png', __dataUrl: 'data:image/png;base64,THUMB2' });
  await flush();
  await flush();
  const replaced = String(elementFor('mrAnswer').value || '');
  check('替换模式覆盖整段解析',
    replaced.indexOf('旧的解析') < 0 && replaced.indexOf('覆盖后的解析。 [cleaned]') >= 0, replaced);
  check('替换结果同样落库', String(lastPut().answer_markdown || '').indexOf('覆盖后的解析。') >= 0,
    JSON.stringify(lastPut()));

  // ---- 失败：一个字都不许写 ----
  sandbox.setReviewOcrMode('append');
  elementFor('mrAnswer').value = '原解析一个字都不许动。';
  requests.length = 0;
  ocrCalls.length = 0;
  ocrScript = [{ status: 'error', message: '识图引擎没配 key' }];
  sandbox.runReviewAnswerOcr({ type: 'image/png' });
  await flush();
  await flush();
  check('识别失败不回写解析框',
    elementFor('mrAnswer').value === '原解析一个字都不许动。', elementFor('mrAnswer').value);
  check('识别失败不发保存请求（不能把空内容存上去）',
    recordPuts().length === 0, String(recordPuts().length));
  check('失败后回到可重新上传的态',
    !elementFor('mrAnswerOcrIdle').classList.contains('hidden') &&
    elementFor('mrAnswerOcrDone').classList.contains('hidden'));

  // ---- 识别出空内容：同样不动 ----
  requests.length = 0;
  ocrCalls.length = 0;
  ocrScript = [{ status: 'success', latex: '   ' }];
  sandbox.runReviewAnswerOcr({ type: 'image/png' });
  await flush();
  await flush();
  check('识别结果为空时也不碰解析框',
    elementFor('mrAnswer').value === '原解析一个字都不许动。', elementFor('mrAnswer').value);
  check('空结果不发保存请求', recordPuts().length === 0, String(recordPuts().length));

  // ---- 非法文件：连接口都不打 ----
  ocrCalls.length = 0;
  sandbox.runReviewAnswerOcr({ type: 'text/plain' });
  await flush();
  check('不是图片直接挡下（不打接口）', ocrCalls.length === 0, String(ocrCalls.length));

  // ---- 换题：识别区回初始态，不残留上一题 ----
  ocrCalls.length = 0;
  ocrScript = [{ status: 'success', latex: '这一题的解析。' }];
  sandbox.runReviewAnswerOcr({ type: 'image/png', __dataUrl: 'data:image/png;base64,THUMB3' });
  await flush();
  check('识别完缩略图停在这一题的图上',
    elementFor('mrAnswerOcrThumb').src === 'data:image/png;base64,THUMB3');
  sandbox.selectMistakeReviewRecord(32);
  await flush();
  check('换题后识别区回初始态（不留上一题的缩略图与「已追加 N 字」）',
    !elementFor('mrAnswerOcrIdle').classList.contains('hidden') &&
    elementFor('mrAnswerOcrDone').classList.contains('hidden'),
    'idle隐藏=' + elementFor('mrAnswerOcrIdle').classList.contains('hidden') +
    ' done隐藏=' + elementFor('mrAnswerOcrDone').classList.contains('hidden'));

  // ---- 没选题：不上传 ----
  sandbox.closeMistakeReview();
  await flush();
  ocrCalls.length = 0;
  sandbox.runReviewAnswerOcr({ type: 'image/png' });
  await flush();
  check('没选题时不上传，只提示先选一道', ocrCalls.length === 0 &&
    textOf('toastMessage').indexOf('先在左侧选一道题') >= 0, textOf('toastMessage'));

  // ---- 在识别/排队中的题不许写回（服务端马上要用识别结果覆写它） ----
  sandbox.openMistakeReview();
  await flush();
  store.recognizeLive.active = true;
  store.recognizeLive.queue = [32];
  store.recognizeLive.done = [];
  store.recognizeLive.current = 32;
  sandbox.selectMistakeReviewRecord(32);
  await flush();
  ocrCalls.length = 0;
  sandbox.runReviewAnswerOcr({ type: 'image/png' });
  await flush();
  check('正在识别的这道题不吃截图识别（写了也会被识别结果冲掉）',
    ocrCalls.length === 0 && textOf('toastMessage').indexOf('正在识别中') >= 0,
    ocrCalls.length + ' | ' + textOf('toastMessage'));
  store.recognizeLive.active = false;
  store.recognizeLive.queue = [];
  store.recognizeLive.current = null;

  ocrScript = [];
  detailPages = null;

  // ============ [16] 保存往返窗口：乐观写回 + 跨记录守卫 ============
  //
  // 保存是「发 PUT → 等响应 → 再更新本地」。窗口内只要发生重绘，读到的都是还没收到
  // 响应的旧记录。触发方式极平常：改完立刻点一下左侧列表（700ms 防抖还没到期）。
  // 旧实现有两个症状，都在这里卡住：
  //   a) 点回同一条 → 输入框被旧值覆盖，屏幕上「刚敲的字没了」；
  //   b) 切到另一条 → 旧题的响应把比对基准写成旧题的值，新题的识别结果再也回填不上。
  console.log('\n[16] 保存往返窗口（乐观写回 / 跨记录守卫）');

  // ---- a) 同一条：窗口内不能回滚 ----
  reset(MATH_RECORDS);
  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(31);
  await flush();
  const typed = '第四题，求导。 追加一句。';
  elementFor('mrContent').value = typed;
  deferPuts = true;
  sandbox.selectMistakeReviewRecord(31);   // 内部 flush 发出 PUT（挂起）后立刻重绘
  check('窗口内点回同一条：输入框仍是用户最新文本',
    elementFor('mrContent').value === typed, elementFor('mrContent').value);
  check('确实有一个 PUT 挂在往返途中', pendingPuts.length === 1,
    JSON.stringify(pendingPuts.map(function (p) { return p.id; })));
  check('发出去的请求体带的是用户新文本，不是旧值',
    pendingPuts[0] && pendingPuts[0].body.content === typed,
    JSON.stringify(pendingPuts[0] && pendingPuts[0].body));

  releasePuts();
  await flush();
  check('响应回来后输入框仍是用户文本（没有回退）',
    elementFor('mrContent').value === typed, elementFor('mrContent').value);

  // 回退一旦发生，用户再动一下就会把旧值写回库 —— 这是真正丢改动的那一步
  deferPuts = true;
  elementFor('mrContent').value = typed + ' 再补一句。';
  sandbox.scheduleMistakeReviewSave();
  runTimers();
  check('后续编辑提交的是最新文本，不是被回退的旧值',
    pendingPuts.length === 1 && pendingPuts[0].body.content === typed + ' 再补一句。',
    JSON.stringify(pendingPuts[0] && pendingPuts[0].body));
  releasePuts();
  await flush();
  deferPuts = false;

  // ---- b) 跨记录：旧题响应不得污染新题的比对基准 ----
  reset(MATH_RECORDS);
  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(31);
  await flush();
  elementFor('mrContent').value = '31 的新题面。';
  deferPuts = true;
  sandbox.selectMistakeReviewRecord(32);   // 31 的 PUT 挂在途中，界面切到 32
  check('切走后输入框换成 32 的题面',
    elementFor('mrContent').value === MATH_RECORDS[1].content, elementFor('mrContent').value);

  releasePuts();
  await flush();
  check('31 的响应回来不改 32 的输入框',
    elementFor('mrContent').value === MATH_RECORDS[1].content, elementFor('mrContent').value);
  check('31 的响应回来不在 32 上写「已保存」（那句说的是别的题）',
    String(elementFor('mrSaveState').textContent || '').indexOf('已保存') < 0,
    String(elementFor('mrSaveState').textContent || '(空)'));
  deferPuts = false;

  // 基准被污染时，这一条会挂：refreshReviewLive 看到「输入框 ≠ 基准」就以为用户
  // 还在编辑，于是拒绝回填 —— 识别结果永远落不进编辑区，且没有任何报错。
  serverRecords = MATH_RECORDS.map(function (r) {
    if (r.id === 32) return Object.assign({}, r, { content: '32 的识别结果', recognize_status: 'done' });
    return Object.assign({}, r);
  });
  taskScript = [
    { status: 'running', progress: 90, steps: [], log: '识别中…',
      recognize_queue: [32], recognize_done: [32], recognize_current: null }
  ];
  sandbox.recognizeOneMistake(32);
  await flush();
  runIntervals();
  await flush();
  check('32 的识别结果能回填进编辑区（基准没被 31 污染）',
    elementFor('mrContent').value === '32 的识别结果', elementFor('mrContent').value);
  store.recognizeLive.active = false;
  store.recognizeLive.queue = [];
  store.recognizeLive.done = [];
  store.recognizeLive.current = null;
  taskScript = [];

  // ---- c) 失败路径：不许假装已保存，也不许把用户改动吞掉 ----
  // 上一段可能留下挂起的 PUT 与在途请求，先清干净；计数一律用增量，不用绝对值
  // —— 绝对值会被上一段漏过来的请求算进去，红得没有道理（假信号比漏检更坏）。
  pendingPuts.length = 0;
  deferPuts = false;
  failPuts = false;
  reset(MATH_RECORDS);
  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(31);
  await flush();

  const putBase = recordPuts().length;
  failPuts = true;
  elementFor('mrContent').value = '这段保存会失败。';
  sandbox.scheduleMistakeReviewSave();
  runTimers();
  await flush();
  const afterFail = recordPuts().length;
  check('失败后不显示「已保存」',
    String(elementFor('mrSaveState').textContent || '').indexOf('已保存') < 0,
    String(elementFor('mrSaveState').textContent || '(空)'));
  check('失败时确实发出过 PUT', afterFail === putBase + 1, putBase + ' -> ' + afterFail);

  // 关键：乐观写回必须跟着回滚。不回滚的话记录内容已经等于输入框，下一次保存
  // 算出「没有变化」直接 return —— 用户再点一次也永远存不上，且全程无提示。
  failPuts = false;
  sandbox.scheduleMistakeReviewSave();
  runTimers();
  await flush();
  check('失败后同一份内容再存一次会重发（基准已还原）',
    recordPuts().length === afterFail + 1, afterFail + ' -> ' + recordPuts().length);

  // ============ [17] 请求失败后按钮必须能恢复 ============
  // 需求（修复 5）：fetch 在网络层失败时是 reject，不是「ok:false 的响应」。这两个流程
  // 都写成「先把按钮置灰 → 请求 → 在 .then 里恢复」，没有拒绝分支的话恢复那一步永远
  // 不执行，按钮就永久停在「正在编译… / 保存中…」，用户只能刷新页面重来。
  console.log('\n[17] 请求失败（断网 / 服务已停）后按钮不能卡死');
  reset([
    rec(22, {
      page_no: 2, question_no: '6', include_in_handout: true, recognize_status: 'done',
      content: '第二题，没有占位符。', figure_images: []
    })
  ]);
  cropCalls.length = 0;
  elementFor('mrCropModal').classList.add('hidden');
  // 假 DOM 没有布局系统：沿用 [13] 钉好的页图显示尺寸
  cropImg.clientWidth = 400;
  cropImg.clientHeight = 566;
  elementFor('mrCropWrapper').clientWidth = 640;
  elementFor('mrCropWrapper').clientHeight = 480;
  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(22);
  await flush();
  sandbox.openMistakeCropModal('figure_images');
  cropImg.onload();
  dragOn(20, 20, 200, 120);

  const cropBtn = elementFor('mrCropConfirmBtn');
  cropBtn.disabled = false;
  elementFor('toastMessage').textContent = '';

  // ---- ① 裁图接口网络失败 ----
  requests.length = 0;
  rejectPatterns = [/\/crop-figure$/];
  sandbox.submitMrCrop();
  await flush();
  rejectPatterns = [];
  check('①截图请求失败后确认按钮恢复可点（不再永久置灰）', cropBtn.disabled === false, String(cropBtn.disabled));
  check('①截图请求失败会明说失败原因', textOf('toastMessage').indexOf('插入配图失败') >= 0, textOf('toastMessage'));
  check('①截图请求失败时弹窗不关闭（用户还能重试）',
    elementFor('mrCropModal').classList.contains('hidden') === false);
  check('①截图请求失败时编辑区没被改动',
    String(elementFor('mrContent').value || '').indexOf('crop_test') < 0, String(elementFor('mrContent').value));

  // ---- ② 裁图成功、但落库失败：不能当成插好了 ----
  cropCalls.length = 0;
  failPuts = true;
  const beforeContent = String(elementFor('mrContent').value || '');
  requests.length = 0;
  sandbox.submitMrCrop();
  await flush();
  failPuts = false;
  check('②发出了裁图请求（前置确认）', cropCalls.length === 1, String(cropCalls.length));
  check('②落库失败后确认按钮同样恢复可点', cropBtn.disabled === false, String(cropBtn.disabled));
  check('②落库失败时不改编辑区（不能假装图已经进正文了）',
    String(elementFor('mrContent').value || '') === beforeContent,
    JSON.stringify(String(elementFor('mrContent').value || '')));
  check('②落库失败时不关弹窗', elementFor('mrCropModal').classList.contains('hidden') === false);

  // ---- ③ 成功路径仍然正常 ----
  sandbox.submitMrCrop();
  await flush();
  check('③落库成功后编辑区确实写进了这张图',
    String(elementFor('mrContent').value || '').indexOf('crop_test') >= 0,
    String(elementFor('mrContent').value || ''));
  check('③落库成功后弹窗关闭', elementFor('mrCropModal').classList.contains('hidden') === true);
  // 重开一次：按钮的可用性必须交回框选状态（没有框就不能点确认），不能残留上一轮的态
  sandbox.openMistakeCropModal('figure_images');
  cropImg.onload();
  check('③重开弹窗、还没框选时确认按钮是禁用的（按钮归属框选状态）',
    cropBtn.disabled === true, String(cropBtn.disabled));
  dragOn(20, 20, 200, 120);
  check('③重开弹窗后重新拖出框即可确认（上一轮没把它永久锁死）',
    cropBtn.disabled === false, String(cropBtn.disabled));
  sandbox.closeMistakeCropModal();

  // ---- ④ 生成 PDF 网络失败 ----
  const exportBtn = elementFor('mistakeExportBtn');
  exportBtn.disabled = false;
  exportBtn.textContent = '生成 PDF';
  elementFor('mistakeExportResult').classList.add('hidden');
  elementFor('mistakeExportResult').textContent = '';
  elementFor('toastMessage').textContent = '';
  requests.length = 0;
  rejectPatterns = [/\/export$/];
  sandbox.exportMistakeHandout();
  await flush();
  rejectPatterns = [];
  check('④生成 PDF 失败后按钮恢复可点', exportBtn.disabled === false, String(exportBtn.disabled));
  check('④生成 PDF 失败后按钮文案恢复「生成 PDF」', exportBtn.textContent === '生成 PDF', String(exportBtn.textContent));
  check('④生成 PDF 失败时结果框可见并写明失败',
    elementFor('mistakeExportResult').classList.contains('hidden') === false &&
    textOf('mistakeExportResult').indexOf('生成失败') >= 0,
    textOf('mistakeExportResult'));
  check('④生成 PDF 失败时也给了 toast', textOf('toastMessage').indexOf('失败') >= 0, textOf('toastMessage'));

  // ============ [18] 审校页行内「移出本次收录」（2026-09-18） ============
  // 需求：人站在审校页上，到这一步才决定某道题本次不要。改动前这一页零移除入口 ——
  // 想剔除必须退回批次详情页。这条链真正的风险不在「按钮有没有」，而在移出之后：
  //   ①只改前端不落库 → 下次进这一页它又冒出来；
  //   ②列表少了、入库仍带着它 → 静默多入库（本项目最怕的错法）；
  //   ③编辑器停在一道已经不在列表里的题上 → 看着像没移成功。
  console.log('\n[18] 行内「移出本次收录」');
  const withContent = function () {
    return BASE_RECORDS.map(function (r) {
      return r.include_in_handout ? Object.assign({}, r, { content: '有题面 ' + r.id }) : Object.assign({}, r);
    });
  };

  reset(withContent());
  sandbox.openMistakeReview();
  await flush();

  // ---- ① 行结构与入口 ----
  const rowHtml = String(elementFor('mrList').innerHTML || '');
  check('①每行都带「移出本次收录」入口',
    rowHtml.indexOf('removeMistakeReviewRecord(11)') >= 0 &&
    rowHtml.indexOf('removeMistakeReviewRecord(13)') >= 0 &&
    rowHtml.indexOf('removeMistakeReviewRecord(14)') >= 0, rowHtml.slice(0, 160));
  // button 套 button 是非法 HTML：浏览器会把内层按钮提到行外面，排版错位、点不着。
  // 所以行外层必须是 div（旧写法是 <button ... onclick="selectMistakeReviewRecord(..)">）。
  check('①行外层已不是 button', rowHtml.indexOf('<button type="button" onclick="selectMistakeReviewRecord(') < 0, rowHtml.slice(0, 160));
  check('①选中动作挪到 div[role=button] 上', rowHtml.indexOf('<div role="button" tabindex="0" onclick="selectMistakeReviewRecord(11)"') >= 0, rowHtml.slice(0, 200));
  check('①× 不会顺带触发选中（stopPropagation 在）', rowHtml.indexOf('event.stopPropagation();removeMistakeReviewRecord(') >= 0);
  // 结构核对的口子：假 DOM 只能看字符串，看不到浏览器把「button 套 button」里的内层
  // 按钮提出来这件事。把真实生成的行 HTML 落盘，交给 mistake_review_row_html_check.js
  // 用 jsdom 解析 + 派发真实点击来核对嵌套形状与事件传播。
  if (process.env.MR_ROW_HTML_OUT) {
    fs.writeFileSync(process.env.MR_ROW_HTML_OUT, rowHtml, 'utf8');
  }

  // ---- ② 移出一道「不在编辑中」的题：落库 + 列表 + 入库口径 ----
  sandbox.selectMistakeReviewRecord(13);
  requests.length = 0;
  sandbox.removeMistakeReviewRecord(14);
  await flush();
  check('②移出发出一次 PUT', recordPuts().length === 1, String(recordPuts().length));
  check('②PUT 打在被移出的 14 上', /^\/api\/mistakes\/records\/14$/.test(recordPuts()[0].url), recordPuts()[0].url);
  const removePut = JSON.parse(recordPuts()[0].options.body || '{}');
  check('②请求体只有收录开关，别的字段一个字不动',
    JSON.stringify(Object.keys(removePut).sort()) === '["include_in_handout"]' && removePut.include_in_handout === false,
    JSON.stringify(removePut));
  check('②本地记录同步变成未收录',
    store.records.filter(function (r) { return r.id === 14; })[0].include_in_handout === false);
  const afterRemove = String(elementFor('mrList').innerHTML || '');
  check('②14 已从列表消失', afterRemove.indexOf('removeMistakeReviewRecord(14)') < 0, afterRemove.slice(0, 200));
  check('②其余两道还在',
    afterRemove.indexOf('removeMistakeReviewRecord(11)') >= 0 && afterRemove.indexOf('removeMistakeReviewRecord(13)') >= 0);
  check('②正在编辑的那道没被换掉', elementFor('mrContent').value === '有题面 13', String(elementFor('mrContent').value));

  // 核心收益：移出即刻收紧入库口径。只从列表删、入库仍带着它，就是静默多入库。
  requests.length = 0;
  sandbox.generateReviewHandout();
  await flush();
  check('②移出后入库不再带上它（record_ids 为 [11,13]）',
    JSON.stringify(lastImportIds()) === '[11,13]', JSON.stringify(lastImportIds()));

  // ---- ③ 移出「正在编辑」的那道：编辑器顺位切走，不落空 ----
  reset(withContent());
  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(13);
  requests.length = 0;
  sandbox.removeMistakeReviewRecord(13);
  await flush();
  check('③编辑器顺位切到原位置的那道（不留在已移出的题上）',
    elementFor('mrContent').value === '有题面 14', String(elementFor('mrContent').value));

  // 移出「正在编辑且有未保存改动」的那道：草稿必须先落盘 —— 移除后编辑器换了题，
  // 防抖里那份字再也没人触发保存，等于直接丢。
  reset(withContent());
  sandbox.openMistakeReview();
  await flush();
  sandbox.selectMistakeReviewRecord(13);
  elementFor('mrContent').value = '刚敲了一半的题面';
  sandbox.scheduleMistakeReviewSave();
  requests.length = 0;
  sandbox.removeMistakeReviewRecord(13);
  await flush();
  const putBodies13 = recordPuts()
    .filter(function (r) { return /^\/api\/mistakes\/records\/13$/.test(r.url); })
    .map(function (r) { return JSON.parse(r.options.body || '{}'); });
  check('③被移出的题若正在编辑，草稿照样先落盘',
    putBodies13.some(function (b) { return b.content === '刚敲了一半的题面'; }),
    JSON.stringify(putBodies13));
  check('③收录开关那次 PUT 也没漏（两件事都在，互不覆盖）',
    putBodies13.some(function (b) { return b.include_in_handout === false; }),
    JSON.stringify(putBodies13));

  // ---- ④ 剔空：中/右栏清干净，并给出回收指引 ----
  reset(withContent());
  sandbox.openMistakeReview();
  await flush();
  sandbox.removeMistakeReviewRecord(11);
  await flush();
  sandbox.removeMistakeReviewRecord(13);
  await flush();
  sandbox.removeMistakeReviewRecord(14);
  await flush();
  check('④剔空后左列表给回收指引（回批次详情重新勾）', textOf('mrList').indexOf('进错题本') >= 0, textOf('mrList'));
  check('④剔空后题面框清空', elementFor('mrContent').value === '', JSON.stringify(elementFor('mrContent').value));
  check('④剔空后解析框清空', elementFor('mrAnswer').value === '', JSON.stringify(elementFor('mrAnswer').value));
  check('④剔空后预览区清空', String(elementFor('mrPreview').innerHTML || '') === '', String(elementFor('mrPreview').innerHTML).slice(0, 120));
  check('④剔空后入库按钮禁用', elementFor('mrGenerateBtn').disabled === true, String(elementFor('mrGenerateBtn').disabled));
  check('④toast 说明去哪儿能勾回来',
    textOf('toastMessage').indexOf('移出本次收录') >= 0 && textOf('toastMessage').indexOf('批次详情') >= 0,
    textOf('toastMessage'));

  // ---- ⑤ 已入库的题也能移出，但必须说清题库那份没动 ----
  reset(withContent());
  sandbox.openMistakeReview();
  await flush();
  elementFor('toastMessage').textContent = '';
  elementFor('toastMessage').innerHTML = '';
  sandbox.removeMistakeReviewRecord(14);
  await flush();
  check('⑤已入库的题移出后，提示点明题库里那份不受影响',
    textOf('toastMessage').indexOf('题库里已入库的那道不受影响') >= 0, textOf('toastMessage'));

  // ---- ⑥ 落库失败：不能假装已经移出 ----
  reset(withContent());
  sandbox.openMistakeReview();
  await flush();
  failPuts = true;
  sandbox.removeMistakeReviewRecord(14);
  await flush();
  failPuts = false;
  check('⑥落库失败时 14 仍留在列表里（列表不许比后端跑得快）',
    String(elementFor('mrList').innerHTML || '').indexOf('removeMistakeReviewRecord(14)') >= 0,
    String(elementFor('mrList').innerHTML || '').slice(0, 160));
  check('⑥落库失败时给出失败提示', textOf('toastMessage').indexOf('失败') >= 0, textOf('toastMessage'));

  console.log('\n' + (fail === 0 ? '全部通过：' + pass + ' 项' : '失败 ' + fail + ' 项 / 共 ' + (pass + fail) + ' 项'));
  process.exit(fail === 0 ? 0 : 1);
}

main().catch(function (err) {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
