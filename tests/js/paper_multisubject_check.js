/**
 * 组卷工作台「一科 → 多科混卷」端到端校验 —— 真实执行 paper.js，不靠正则猜源码。
 *
 * 为什么要有这个脚本：
 *   「数学」这个词以前在卷面上是写死的（抬头学科行、页脚前缀、LaTeX \subject），
 *   但一份卷子完全可以同时含数学 / 物理 / 化学。改动把学科变成可编辑 + 按学科分部分，
 *   代价是「单科卷的排版可能被顺手改坏」和「混科卷的题号可能跳号」这两个新风险。
 *   静态读源码证明不了这些，所以这里用假 DOM 把 paper.js 整个加载进 vm 沙箱，
 *   真跑 renderPaperCanvas / renderPaperWorkspace / togglePaperFilterSubject，断言渲染结果：
 *
 *   [1] 单科卷：结构与改动前一致 —— 不出现「第 N 部分」、不插 data-subject、
 *       页脚仍是「数学 &nbsp; 第 x 页」，说明零回归；
 *   [2] 混科卷：两级结构（学科 → 题型）、学科顺序固定 数学→物理→化学（与入卷顺序无关）、
 *       大题序号与题号全卷连续；
 *   [3] 混科卷 + exam_19：自动降级成普通考试卷，并给出可见提示（否则用户以为排版坏了）；
 *   [4] 选题侧：学科 chips 至少保留一科、题卡角标只在多科时出现、请求带 subject 过滤、
 *       抬头按题库当前学科预填一次。
 *
 * 用法: node tests/js/paper_multisubject_check.js [paper.js 路径]
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

/** 按顶层 data-vcard 把卡片的 HTML 切成一段一段（每张卡一段）。 */
function splitVCards(html) {
  const re = /<div[^>]*\bdata-vcard\b[^>]*>/g;
  const starts = [];
  let m;
  while ((m = re.exec(html)) !== null) starts.push(m.index);
  const parts = [];
  for (let i = 0; i < starts.length; i++) {
    parts.push(html.slice(starts[i], i + 1 < starts.length ? starts[i + 1] : html.length));
  }
  return parts;
}

function makeElement(id, getById) {
  const attrs = {};
  const classes = new Set();
  const el = {
    id: id,
    _innerHTML: '',
    textContent: '',
    innerText: '',
    value: '',
    scrollTop: 0,
    scrollHeight: 4000,
    clientHeight: 800,
    offsetHeight: 0,
    style: {},
    dataset: {},
    handlers: {},
    children: [],
    parentNode: null,
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
    addEventListener: function (t, f) { (el.handlers[t] = el.handlers[t] || []).push(f); },
    removeEventListener: function () {},
    // 窗口同步靠这些树操作只增删差异卡片，假 DOM 必须真的维护 children，
    // 否则「复用了哪几张、重建了哪几张」在夹具里完全看不见。
    appendChild: function (node) {
      if (!node) return node;
      const existing = el.children.indexOf(node);
      if (existing >= 0) el.children.splice(existing, 1);
      el.children.push(node);
      node.parentNode = el;
      if (!node.offsetHeight) node.offsetHeight = 264;
      return node;
    },
    insertBefore: function (node, ref) {
      if (!node) return node;
      const existing = el.children.indexOf(node);
      if (existing >= 0) el.children.splice(existing, 1);
      const at = ref ? el.children.indexOf(ref) : -1;
      if (at >= 0) el.children.splice(at, 0, node); else el.children.push(node);
      node.parentNode = el;
      return node;
    },
    removeChild: function (node) {
      const i = el.children.indexOf(node);
      if (i >= 0) { el.children.splice(i, 1); node.parentNode = null; }
      return node;
    },
    remove: function () { if (el.parentNode) el.parentNode.removeChild(el); },
    focus: function () {},
    blur: function () {},
    click: function () {},
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: 1000, height: 2000, right: 1000, bottom: 2000 }; }
  };
  Object.defineProperty(el, 'firstElementChild', {
    get: function () { return el.children[0] || null; }
  });
  Object.defineProperty(el, 'nextElementSibling', {
    get: function () {
      const p = el.parentNode;
      if (!p) return null;
      const i = p.children.indexOf(el);
      return i >= 0 ? (p.children[i + 1] || null) : null;
    }
  });
  Object.defineProperty(el, 'innerHTML', {
    get: function () {
      // 卡片是 appendChild 进去的，读 innerHTML 得按子元素序列化回来，
      // 否则「渲染出了什么」在夹具里看不见。
      if (el.children.length) return el.children.map(function (c) { return c._innerHTML; }).join('');
      return el._innerHTML;
    },
    set: function (html) {
      el._innerHTML = String(html);
      el.children = [];
      const parts = splitVCards(el._innerHTML);
      for (let i = 0; i < parts.length; i++) {
        const child = makeElement('fake-card-' + i, getById);
        child._innerHTML = parts[i];
        child.parentNode = el;
        el.children.push(child);
      }
      // 真实浏览器里 container.innerHTML 重建会连旧的 spacer/viewport 一起丢掉，
      // 新 viewport 是空的。假 DOM 必须照做，否则窗口同步会以为上一批卡片还在。
      if (typeof getById === 'function' && /id="paperVirtualViewport"/.test(el._innerHTML)) {
        const vp = getById('paperVirtualViewport');
        if (vp) { vp.children = []; vp._innerHTML = ''; }
      }
      if (typeof getById === 'function' && /id="paperVirtualSpacer"/.test(el._innerHTML)) {
        const sp = getById('paperVirtualSpacer');
        if (sp) sp.style = {};
      }
    }
  });
  return el;
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

/** 建一个装了真 paper.js 的沙箱。opts.bankSubject 决定「题库当前学科」。 */
function boot(opts) {
  opts = opts || {};
  const elements = {};
  function elementFor(id) {
    if (!elements[id]) elements[id] = makeElement(id, elementFor);
    return elements[id];
  }
  const documentHandlers = {};
  const requests = [];
  let fetchImpl = function () {
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve([]); } });
  };
  const toasts = [];

  const sandbox = {
    console: console,
    setTimeout: function (fn) { if (typeof fn === 'function') fn(); return 0; },
    clearTimeout: function () {},
    setInterval: function () { return 0; },
    clearInterval: function () {},
    fetch: function (url, options) {
      requests.push({ url: String(url), options: options || {} });
      return fetchImpl(String(url), options || {});
    },
    localStorage: makeLocalStorage({}),
    document: {
      getElementById: elementFor,
      querySelector: function () { return null; },
      querySelectorAll: function () { return []; },
      createElement: function () { return makeElement('created', elementFor); },
      addEventListener: function (t, f) { (documentHandlers[t] = documentHandlers[t] || []).push(f); },
      removeEventListener: function () {},
      body: makeElement('body'),
      documentElement: { classList: { add: function () {}, remove: function () {} } },
      activeElement: null
    }
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  // index.html 里 api.js 先于 paper.js 加载，提供 window.MathBankSafe；这里按同一契约放替身，
  // 否则渲染卷面会撞 undefined.escapeHtml。替身只管形状，不测消毒。
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
  sandbox.MathRender = { render: function () {}, renderMathIn: function () {} };
  // vm 的新上下文没有宿主全局；fetchBankQuestions 靠 URLSearchParams 拼查询串，
  // 缺了它抛 ReferenceError 是**沙箱缺口**，不是被测代码的问题。
  sandbox.URLSearchParams = URLSearchParams;
  sandbox.bankSubject = opts.bankSubject || 'math';
  sandbox.systemMetadata = { subject_curriculum: {} };
  sandbox.showToast = function (msg) { toasts.push(String(msg)); };

  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'paper.js' });

  return {
    sandbox: sandbox,
    elements: elements,
    requests: requests,
    toasts: toasts,
    canvasHtml: function () { return elementFor('paperCanvasSection').innerHTML; },
    filterHtml: function () { return elementFor('paperFilterSection').innerHTML; },
    streamHtml: function () { return elementFor('paperVirtualViewport').innerHTML; },
    // 真实页面里 DOMContentLoaded 会跑 loadStateFromStorage()，抬头预填的「只补一次」
    // 开关就在那里打开。不补这一步，沙箱比浏览器少跑一段启动流程。
    boot: function () { (documentHandlers['DOMContentLoaded'] || []).forEach(function (fn) { fn(); }); },
    setFetch: function (impl) { fetchImpl = impl; }
  };
}

// ---------------------------------------------------------------- 断言工具

/** 按出现位置把「学科层标题」与「题型层标题」摊成一份大纲 */
function outline(html) {
  const marks = [];
  let m;
  const rePart = /第([一二三四五六七八九十]+)部分　([^（<]{1,10})（共 (\d+) 题，共 (\d+) 分）/g;
  while ((m = rePart.exec(html)) !== null) {
    marks.push({ kind: 'part', index: m.index, num: m[1], label: m[2], count: +m[3], score: +m[4] });
  }
  const reSec = /([一二三四五六七八九十]+)、(选择题|多选题|填空题|解答题)：/g;
  while ((m = reSec.exec(html)) !== null) {
    marks.push({ kind: 'section', index: m.index, num: m[1], name: m[2] });
  }
  marks.sort(function (a, b) { return a.index - b.index; });
  return marks;
}

function parts(html) { return outline(html).filter(function (x) { return x.kind === 'part'; }); }
function secs(html) { return outline(html).filter(function (x) { return x.kind === 'section'; }); }
function kinds(html) { return outline(html).map(function (x) { return x.kind; }); }

/** 卷面上真实印出来的题号 */
function questionNums(html) {
  const out = [];
  const re = /class="font-bold mr-1 text-slate-900 shrink-0">(\d+)\.<\/span>/g;
  let m;
  while ((m = re.exec(html)) !== null) out.push(+m[1]);
  return out;
}

/** 每张 A4 纸的页脚文案 */
function footers(html) {
  const out = [];
  const re = /<div class="absolute bottom-5 left-0 right-0 text-center text-xs font-serif text-slate-700 tracking-wider">\s*([\s\S]*?)\s*<\/div>/g;
  let m;
  while ((m = re.exec(html)) !== null) out.push(m[1].replace(/\s+/g, ' ').trim());
  return out;
}

function fakeQuestion(id, type, subject, content) {
  return {
    id: id, seq_num: id, question_type: type, difficulty: 'normal', subject: subject,
    content: content || ('第 ' + id + ' 题：已知函数 f(x)=x^2-2x，求其最小值。'),
    knowledge_list: '函数'
  };
}

const CH = '在每小题给出的四个选项中，只有一项是符合题目要求的。……（题干）';
const DA = '解答下列各题，写出必要的文字说明、证明过程或演算步骤。……（题干）';

function setMeta(env, patch) {
  Object.keys(patch).forEach(function (k) { env.sandbox.PaperStore.meta[k] = patch[k]; });
}

function render(env, cart) {
  env.sandbox.PaperStore.cart = cart;
  env.sandbox.renderPaperCanvas();
  return env.canvasHtml();
}

// ---------------------------------------------------------------- 用例

(async function main() {
  // ============================================================ [1] 单科卷零回归
  section('[1] 单科卷结构与改动前一致（不插学科层、不重置题号）');
  const s1 = boot();
  s1.sandbox.PaperStore.questionsMap = {
    101: fakeQuestion(101, 'single_choice', 'math', CH),
    102: fakeQuestion(102, 'detailed_answer', 'math', DA)
  };
  setMeta(s1, { paper_type: 'exam_19', subject_line: '数学', exam_duration: 120 });
  let html = render(s1, [{ id: 101, score: 5 }, { id: 102, score: 12 }]);

  check('单科卷不出现「第 N 部分」学科层标题',
    parts(html).length === 0, JSON.stringify(parts(html)));
  check('单科卷不往题目卡片上挂 data-subject',
    html.indexOf('data-subject=') < 0);
  check('单科卷仍走 exam_19 题号锚点（单选 1、解答 15）',
    JSON.stringify(questionNums(html)) === JSON.stringify([1, 15]),
    JSON.stringify(questionNums(html)));
  check('抬头学科行印的是用户填的「数学」',
    html.indexOf('canvas-meta-subject') >= 0 && html.indexOf('>数学</div>') >= 0);
  const f1 = footers(html);
  check('页脚仍带学科前缀「数学 &nbsp; 第 x 页」',
    f1.length > 0 && f1[0].indexOf('数学 &nbsp; 第 1 页') === 0, JSON.stringify(f1));

  setMeta(s1, { subject_line: '' });
  html = render(s1, [{ id: 101, score: 5 }, { id: 102, score: 12 }]);
  const f1b = footers(html);
  check('抬头留空后页脚只剩页码（不再写死学科）',
    f1b.length > 0 && f1b[0].indexOf('数学') < 0 && f1b[0].indexOf('第 1 页') >= 0,
    JSON.stringify(f1b));

  // ============================================================ [2] 混科卷两级结构
  section('[2] 混科卷：学科 → 题型 两级结构，顺序固定');
  const s2 = boot();
  s2.sandbox.PaperStore.questionsMap = {
    101: fakeQuestion(101, 'single_choice', 'math', CH),
    102: fakeQuestion(102, 'detailed_answer', 'math', DA),
    201: fakeQuestion(201, 'single_choice', 'physics', CH),
    301: fakeQuestion(301, 'fill_in_blank', 'chemistry', '填空：某有机物的分子式为 C2H6O，……')
  };
  setMeta(s2, { paper_type: 'exam_19', subject_line: '', exam_duration: 150 });
  // 刻意按「化学 → 物理 → 数学」的乱序入卷，验证顺序由学科决定、与入卷顺序无关
  html = render(s2, [{ id: 301, score: 5 }, { id: 201, score: 5 }, { id: 102, score: 12 }, { id: 101, score: 5 }]);

  const p2 = parts(html);
  check('识别出三个学科层标题', p2.length === 3, JSON.stringify(p2));
  check('学科顺序 = 数学 → 物理 → 化学（与入卷顺序无关）',
    p2.map(function (p) { return p.label; }).join('/') === '数学/物理/化学',
    JSON.stringify(p2.map(function (p) { return p.label; })));
  check('部分序号依次为 一 / 二 / 三',
    p2.map(function (p) { return p.num; }).join('') === '一二三',
    JSON.stringify(p2.map(function (p) { return p.num; })));
  check('数学部分统计为 2 题 / 17 分',
    p2[0].count === 2 && p2[0].score === 17, JSON.stringify(p2[0]));
  check('物理部分统计为 1 题 / 5 分',
    p2[1].count === 1 && p2[1].score === 5, JSON.stringify(p2[1]));
  check('化学部分统计为 1 题 / 5 分',
    p2[2].count === 1 && p2[2].score === 5, JSON.stringify(p2[2]));

  check('结构形态 = 部分 / 题型 / 题型 / 部分 / 题型 / 部分 / 题型',
    kinds(html).join(',') === 'part,section,section,part,section,part,section',
    kinds(html).join(','));

  const c2 = secs(html);
  check('题型层序号全卷连续且不重复（一二三四）',
    c2.map(function (x) { return x.num; }).join('') === '一二三四',
    JSON.stringify(c2.map(function (x) { return x.num; })));

  check('题号全卷连续 1..4（混科不走 exam_19 锚点）',
    JSON.stringify(questionNums(html)) === JSON.stringify([1, 2, 3, 4]),
    JSON.stringify(questionNums(html)));
  check('学科层标题带 data-subject，供分页/拖拽识别',
    html.indexOf('data-subject="math"') >= 0
    && html.indexOf('data-subject="physics"') >= 0
    && html.indexOf('data-subject="chemistry"') >= 0);
  check('抬头统计用的考试用时取用户设定的 150 分钟',
    html.indexOf('考试用时 150 分钟') >= 0);

  // 「第 N 部分」标题不许孤零零落在页末
  const pageBreaks = html.split('a4-paper-sheet');
  let orphan = false;
  pageBreaks.forEach(function (pg) {
    const marks = outline(pg);
    if (marks.length > 0 && marks[marks.length - 1].kind === 'part') orphan = true;
  });
  check('没有哪一页以「第 N 部分」标题收尾（分页会把标题带到下一页）', !orphan);

  // ============================================================ [3] exam_19 降级提示
  section('[3] 混科 + exam_19：自动按普通考试卷排版并明确告知');
  check('页面上给出「已按普通考试卷排版」的提示',
    html.indexOf('已按普通考试卷排版') >= 0);
  check('提示解释了原因是 19 题模板题号是数学专用',
    html.indexOf('题号是数学专用') >= 0);
  const s3 = boot();
  s3.sandbox.PaperStore.questionsMap = {
    101: fakeQuestion(101, 'single_choice', 'math', CH),
    102: fakeQuestion(102, 'detailed_answer', 'math', DA)
  };
  setMeta(s3, { paper_type: 'exam_19', subject_line: '数学' });
  const singleHtml = render(s3, [{ id: 101, score: 5 }, { id: 102, score: 12 }]);
  check('单科 + exam_19 不弹这条提示（不要无端吓人）',
    singleHtml.indexOf('已按普通考试卷排版') < 0);

  // ============================================================ [3b] 答题卡入口
  section('[3b] 混科卷收起答题卡入口（A3 答题卡是数学 19 题卷专用，套不上混科）');
  check('混科卷的按钮行里没有「答题卡 PDF 预览」',
    html.indexOf('答题卡 PDF 预览') < 0, html.slice(0, 200));
  check('混科卷的提示里说明了答题卡入口为什么消失',
    html.indexOf('答题卡入口已隐藏') >= 0);
  check('单科卷仍然保留「答题卡 PDF 预览」',
    singleHtml.indexOf('答题卡 PDF 预览') >= 0, '单科卷把答题卡入口也收掉了');

  // ============================================================ [4] 选题侧
  section('[4] 选题侧：学科多选、角标、请求过滤');
  const s4 = boot({ bankSubject: 'physics' });
  s4.sandbox.PaperStore.questionsMap = { 101: fakeQuestion(101, 'single_choice', 'math', CH) };
  check('组卷台学科筛选默认三科全选（等同改动前的全库取题）',
    JSON.stringify(s4.sandbox.PaperStore.filters.subjects) === JSON.stringify(['math', 'physics', 'chemistry']),
    JSON.stringify(s4.sandbox.PaperStore.filters.subjects));

  s4.boot();
  await s4.sandbox.renderPaperWorkspace();
  const chips = s4.filterHtml();
  ['数学', '物理', '化学'].forEach(function (label) {
    check('筛选栏里有「' + label + '」学科 chip', chips.indexOf('>' + label + '</button>') >= 0);
  });
  check('三科全选时 chip 都是选中态（aria-pressed=true）',
    (chips.match(/aria-pressed="true"/g) || []).length === 3,
    String((chips.match(/aria-pressed="true"/g) || []).length));

  s4.sandbox.togglePaperFilterSubject('physics');
  check('取消勾选物理后只剩数学 + 化学',
    JSON.stringify(s4.sandbox.PaperStore.filters.subjects) === JSON.stringify(['math', 'chemistry']),
    JSON.stringify(s4.sandbox.PaperStore.filters.subjects));
  check('取消后 chip 状态同步（只剩 2 个选中）',
    (s4.filterHtml().match(/aria-pressed="true"/g) || []).length === 2,
    s4.filterHtml().slice(0, 300));

  await new Promise(function (r) { setImmediate(r); });
  const qreq = s4.requests.filter(function (x) { return x.url.indexOf('/api/questions') === 0; }).pop();
  check('部分勾选时请求带上 subject 过滤',
    !!qreq && decodeURIComponent(qreq.url).indexOf('subject=math,chemistry') >= 0,
    qreq ? qreq.url : '(没有发出请求)');

  s4.sandbox.togglePaperFilterSubject('math');
  s4.sandbox.togglePaperFilterSubject('chemistry');
  check('全部取消时至少保留一科，并给出提示',
    s4.sandbox.PaperStore.filters.subjects.length === 1
    && s4.toasts.some(function (t) { return t.indexOf('至少') >= 0; }),
    JSON.stringify(s4.sandbox.PaperStore.filters.subjects) + ' / ' + JSON.stringify(s4.toasts));
  check('保留的那一科是取消前最后剩下的那科（化学）',
    JSON.stringify(s4.sandbox.PaperStore.filters.subjects) === JSON.stringify(['chemistry']),
    JSON.stringify(s4.sandbox.PaperStore.filters.subjects));

  // 题卡角标：真跑一次题库列表渲染（虚拟列表把卡片写进 paperVirtualViewport）
  const s5 = boot();
  const qCard = fakeQuestion(101, 'single_choice', 'math', CH);
  s5.sandbox.PaperStore.questionsMap = { 101: qCard };
  s5.setFetch(function () {
    return Promise.resolve({
      ok: true, status: 200,
      json: function () { return Promise.resolve([qCard]); }
    });
  });
  s5.boot();
  s5.sandbox.PaperStore.filters.subjects = ['math'];
  await s5.sandbox.renderPaperWorkspace();
  let stream = s5.streamHtml();
  check('前置：题库列表把题卡渲染出来了', stream.indexOf('#101') >= 0, stream.slice(0, 240));
  check('单科浏览时题卡不挂学科角标（避免一排重复标签）',
    stream.indexOf('所属学科') < 0, stream.slice(0, 240));

  s5.sandbox.PaperStore.filters.subjects = ['math', 'physics'];
  await s5.sandbox.renderPaperWorkspace();
  stream = s5.streamHtml();
  check('多科浏览时题卡挂出「数学」角标',
    stream.indexOf('所属学科') >= 0 && stream.indexOf('>数学</span>') >= 0, stream.slice(0, 400));

  // 抬头预填：只补一次，且只认题库当前学科
  const s6 = boot({ bankSubject: 'chemistry' });
  s6.boot();
  await s6.sandbox.renderPaperWorkspace();
  check('首次进入时抬头按题库当前学科预填（化学）',
    s6.canvasHtml().indexOf('canvas-meta-subject') >= 0 && s6.canvasHtml().indexOf('>化学</div>') >= 0,
    s6.canvasHtml().indexOf('canvas-meta-subject') >= 0 ? '抬头在但没预填' : '抬头没渲染');

  s6.sandbox.updatePaperMeta('subject_line', '数理综合');
  s6.sandbox.PaperStore.meta.subject_line = '数理综合';
  await s6.sandbox.renderPaperWorkspace();
  check('用户改过抬头后重进组卷台不会被预填回填（保用户填的值）',
    s6.canvasHtml().indexOf('>数理综合</div>') >= 0);

  console.log('\n通过 ' + pass + ' 项，失败 ' + fail + ' 项');
  process.exit(fail === 0 ? 0 : 1);
})().catch(function (err) {
  console.log('\n沙箱执行抛出异常：\n' + (err && err.stack ? err.stack : String(err)));
  process.exit(1);
});
