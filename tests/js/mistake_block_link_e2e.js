/**
 * 错题页「左点框 → 右侧对应题块」的**真实 DOM + 真实 HTTP** 端到端。
 *
 * 与 `mistake_box_payload_check.js` 的分工：
 *   - 那个用假 DOM，跑得快、不依赖服务，断言状态与请求体；
 *   - 这个把**真实的 index.html 与真实的 mistake.js** 装进 jsdom，页图与批次数据都
 *     通过真实 HTTP 从跑着的服务取回来，然后真的派发一个 click。
 *
 * 为什么要多这一层：假 DOM 里的 `event.target.closest` 是**手搓的桩**，它按我们的假设
 * 回答「[data-box] 找到了」—— 而这次改的恰恰是这一层。真实节点树里到底解析成哪个元素
 * （框体 div？框上的 ✕ 把手 span？还是被 pointer-events:none 吞掉后落到块上？），
 * 以及 `scrollIntoView` 最终落在哪张卡片上，只有真跑才看得见。
 *
 * 前提：本地服务在跑。
 * 用法: node tests/js/mistake_block_link_e2e.js [baseUrl] [batchId]
 * 退出码: 0 全通过 / 1 有失败 / 2 环境不具备（jsdom 缺失或服务不可达）
 */
let JSDOM;
let VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = require('jsdom'));
} catch (error) {
  console.log('SKIP  找不到 jsdom —— 需要把 NODE_PATH 指向 managed node workspace');
  process.exit(2);
}

const fs = require('fs');
const path = require('path');

const BASE = (process.argv[2] || process.env.MISTAKE_LINK_BASE || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const BATCH_ID = Number(process.argv[3] || process.env.MISTAKE_LINK_BATCH || 1);
// 调试用：把某一支脚本换成磁盘上指定的那份（做反向对照 —— 拿修复前的 mistake.js
// 跑一遍，确认这个夹具确实会红）。目录里没有的文件照常走 HTTP。
const OVERRIDE_JS_DIR = process.env.MISTAKE_LINK_JS_DIR || '';

let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '\n        -> ' + extra : '')); }
}

async function get(url) {
  const res = await fetch(BASE + url);
  if (!res.ok) throw new Error(url + ' → HTTP ' + res.status);
  return res.text();
}

const tick = () => new Promise(function (resolve) { setTimeout(resolve, 0); });

/** 轮询等一个条件成立（真实 HTTP 是异步的，不能靠固定 sleep 猜）。 */
async function waitFor(probe, label, rounds) {
  for (let i = 0; i < (rounds || 200); i += 1) {
    if (probe()) return true;
    await tick();
  }
  console.log('  WARN  等待「' + label + '」超时，后面的断言会因此失真');
  return false;
}

/** 归一化坐标下两个矩形是否同一块（与 mistake.js 的 BOX_FOCUS_MATCH 同容差）。 */
function sameRect(box, record) {
  const x0 = record.block_x_start == null ? 0 : record.block_x_start;
  const x1 = record.block_x_end == null ? 1 : record.block_x_end;
  return Math.abs(box[1] - record.block_y_start) <= 0.004
    && Math.abs(box[3] - record.block_y_end) <= 0.004
    && Math.abs(box[0] - x0) <= 0.02
    && Math.abs(box[2] - x1) <= 0.02;
}

(async function main() {
  let html;
  try {
    html = await get('/static/index.html');
  } catch (error) {
    console.log('SKIP  本地服务不可达：' + BASE + '（' + error.message + '）');
    process.exit(2);
  }

  // runScripts 必须是 'dangerously'：'outside-only' 只开放 window.eval，**不执行**
  // 动态插入的 <script>，注入完页面一片安静、脚本其实一行没跑（第一次就栽在这）。
  // 不传 resources 选项 → jsdom 不加载任何外部资源，正好把 CDN 上的 tailwind / katex
  // 挡在外面：index.html 里那段 tailwind 配置内联脚本会因此报一次 ReferenceError，
  // 与本次改动无关，只记一行 WARN。
  const console_ = new VirtualConsole();
  console_.on('jsdomError', function (error) {
    console.log('  WARN  jsdom: ' + String(error && error.message).slice(0, 160));
  });
  const dom = new JSDOM(html, {
    url: BASE + '/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: console_,
  });
  const win = dom.window;
  const doc = win.document;

  // jsdom 缺的两个观察者：不 stub 会在构造处直接抛错，脚本加载就断在半路
  win.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

  // 「谁被滚进了视野」—— 用户要的「右侧显示出对应题块」在 DOM 层的唯一证据就是它
  const scrolledCards = [];
  win.Element.prototype.scrollIntoView = function (options) {
    scrolledCards.push({
      card: this.getAttribute('data-card'),
      block: options && options.block,
      behavior: options && options.behavior,
    });
  };
  const scrolledPages = [];
  win.Element.prototype.scrollTo = function (options) {
    scrolledPages.push({ id: this.id, top: options && options.top, left: options && options.left });
  };

  // 真实 HTTP：相对路径补上 host。顺带记录所有请求，最后断言「只读不写」。
  const requests = [];
  win.fetch = function (input, init) {
    const raw = typeof input === 'string' ? input : String(input && input.url ? input.url : input);
    const url = raw.indexOf('http') === 0 ? raw : BASE + raw;
    requests.push({ url: url, method: ((init && init.method) || 'GET').toUpperCase() });
    return fetch(url, init);
  };

  // 按 index.html 的顺序注入真实脚本。只装 mistake.js 依赖的两支：它是自包含 IIFE，
  // 不碰 api.js / paper.js / editor.js（那些在 jsdom 里跑起来代价与噪音都大得多）。
  for (const name of ['math-render.js', 'mistake.js']) {
    const override = OVERRIDE_JS_DIR ? path.join(OVERRIDE_JS_DIR, name) : '';
    const source = override && fs.existsSync(override) ? fs.readFileSync(override, 'utf8') : await get('/static/js/' + name);
    const node = doc.createElement('script');
    node.textContent = source;
    doc.body.appendChild(node);
  }
  check('真实 index.html + 真实 mistake.js 已装进 jsdom',
    !!win.MistakeStore && typeof win.openMistakeBatch === 'function');

  const store = win.MistakeStore;

  // jsdom 不做布局，getBoundingClientRect 一律返回 0×0 —— 而 scrollPageToRecord 里
  // 头一句就是 `if (!pageBox.height) return`，不喂尺寸的话「页图滚到这一块」这条
  // 断言永远测不到东西（会变成假通过的反面：假失败）。这里按「页高 1200 / 视口 600」
  // 喂真实形状的矩形，之后 top 就能反算：y0 × 1200。
  const PAGE_PX_HEIGHT = 1200;
  function stubRect(id, rect) {
    const node = doc.getElementById(id);
    if (node) node.getBoundingClientRect = function () { return rect; };
  }
  stubRect('mistakePageImage', { left: 0, top: 0, width: 800, height: PAGE_PX_HEIGHT, right: 800, bottom: PAGE_PX_HEIGHT });
  stubRect('mistakePageScroll', { left: 0, top: 0, width: 800, height: 600, right: 800, bottom: 600 });

  win.openMistakeBatch(BATCH_ID);
  const loaded = await waitFor(function () {
    return store.pages && store.pages.length && store.records && store.records.length;
  }, '批次 ' + BATCH_ID + ' 载入');
  if (!loaded) { console.log('失败 ' + (fail + 1) + ' 项 / 共 ' + (pass + fail + 1) + ' 项'); process.exit(1); }

  check('批次数据经真实 HTTP 取回', store.records.length > 0 && store.pages.length > 0,
    'records=' + store.records.length + ' pages=' + store.pages.length);

  // 找一页「既有手工框、又有框生成的记录」—— 就是用户报的那个场景
  let target = null;
  store.pages.forEach(function (page) {
    if (target) return;
    const boxes = store.manualBoxes[page.page_no] || [];
    const recs = store.records.filter(function (r) { return r.page_no === page.page_no; });
    for (let index = 0; index < boxes.length; index += 1) {
      const hit = recs.filter(function (r) { return sameRect(boxes[index], r); })[0];
      if (hit) { target = { page: page, boxIndex: index, record: hit }; return; }
    }
  });
  if (!target) {
    console.log('SKIP  当前批次里没有「框与记录矩形重合」的样本，这个端到端不适用');
    process.exit(2);
  }
  console.log('  目标：第 ' + target.page.page_no + ' 页 · 框' + (target.boxIndex + 1)
    + ' → 记录 id=' + target.record.id + '（页内块' + (target.record.block_index + 1) + '）');

  // 翻到目标页
  const targetIndex = store.pages.findIndex(function (p) { return p.page_no === target.page.page_no; });
  let guard = 0;
  while (store.pageIndex < targetIndex && guard < store.pages.length + 2) {
    win.mistakePageStep(1);
    guard += 1;
    await tick();
  }
  await tick();
  check('翻到了目标页', store.pageIndex === targetIndex, store.pageIndex + ' / ' + targetIndex);

  const overlay = doc.getElementById('mistakePageOverlay');
  const recordId = String(target.record.id);
  const boxSelector = '[data-box="' + target.boxIndex + '"]';

  const boxBefore = overlay.querySelector(boxSelector);
  check('页图上真的渲染出了这个人工框', !!boxBefore);
  if (!boxBefore) { console.log('失败 ' + (fail + 1) + ' 项 / 共 ' + (pass + fail + 1) + ' 项'); process.exit(1); }

  check('这个框对应的记录在页图上不画块（页图上只有框可点）',
    !overlay.querySelector('[data-record="' + recordId + '"]'));
  check('非画框模式下框体光标是手型（不是 move）',
    (boxBefore.getAttribute('style') || '').indexOf('cursor:pointer') >= 0,
    boxBefore.getAttribute('style'));
  check('非画框模式下 title 写的是「点它＝选中右侧题块」',
    (boxBefore.getAttribute('title') || '').indexOf('点击＝选中右侧对应的题块') >= 0,
    boxBefore.getAttribute('title'));

  const cardBefore = doc.querySelector('[data-card="' + recordId + '"]');
  check('右侧列表里有这张卡片', !!cardBefore);
  check('点之前这张卡片没有选中环',
    !!cardBefore && (cardBefore.className || '').indexOf('ring-brand-500') < 0,
    cardBefore && cardBefore.className);

  scrolledCards.length = 0;
  scrolledPages.length = 0;

  // 真正的点击：走真实节点树的事件冒泡，让 overlay 上的委托处理器接住
  boxBefore.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
  await tick();
  await tick();

  check('点框 → 选中的正是该框对应的记录',
    store.selectedRecordId === target.record.id,
    'selectedRecordId=' + store.selectedRecordId + ' 期望 ' + target.record.id);
  check('右侧对应卡片被滚进视野（这就是「右侧显示出对应题块」）',
    scrolledCards.some(function (c) { return c.card === recordId; }),
    JSON.stringify(scrolledCards));
  check('滚的是这一张，不是别人',
    scrolledCards.length > 0 && scrolledCards[scrolledCards.length - 1].card === recordId,
    JSON.stringify(scrolledCards));

  const cardAfter = doc.querySelector('[data-card="' + recordId + '"]');
  check('右侧对应卡片拿到选中环',
    !!cardAfter && (cardAfter.className || '').indexOf('ring-2 ring-brand-500') >= 0,
    cardAfter && cardAfter.className);
  check('右侧别的卡片没有环',
    Array.prototype.every.call(doc.querySelectorAll('[data-card]'), function (node) {
      return node.getAttribute('data-card') === recordId
        || (node.className || '').indexOf('ring-brand-500') < 0;
    }));

  const boxAfter = doc.getElementById('mistakePageOverlay').querySelector(boxSelector);
  check('页图上该框变成琥珀实线 + 「当前 · 块N」',
    !!boxAfter
    && (boxAfter.getAttribute('style') || '').indexOf('border:2px solid #d97706') >= 0
    && boxAfter.textContent.indexOf('当前 · 块' + (target.record.block_index + 1)) >= 0,
    boxAfter && (boxAfter.textContent + ' | ' + boxAfter.getAttribute('style')));
  check('页图滚动容器被要求滚到该记录的位置',
    scrolledPages.some(function (p) { return p.id === 'mistakePageScroll'; }),
    JSON.stringify(scrolledPages));
  const pageScroll = scrolledPages.filter(function (p) { return p.id === 'mistakePageScroll'; })[0];
  const expectedTop = target.record.block_y_start * PAGE_PX_HEIGHT;
  check('滚动位置就是这一块的纵向位置（y0 × 页高）',
    !!pageScroll && Math.abs(pageScroll.top - expectedTop) < 1,
    JSON.stringify(pageScroll) + ' 期望 top≈' + expectedTop.toFixed(1));

  // 再点一次同一张已选中的卡片：不该反复抖动滚动，但要保持高亮
  scrolledCards.length = 0;
  doc.querySelector('[data-card="' + recordId + '"]')
    .dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
  await tick();
  check('点右侧卡片仍指向同一条记录（反向没被破坏）', store.selectedRecordId === target.record.id);

  // 只读确认：这个夹具只点选，绝不允许写用户数据
  const writes = requests.filter(function (r) { return r.method !== 'GET'; });
  check('全程零写请求（点框只是选中，不改任何数据）',
    writes.length === 0, JSON.stringify(writes));

  console.log('\n' + '='.repeat(60));
  if (fail === 0) {
    console.log('全部通过：' + pass + ' 项');
    process.exit(0);
  }
  console.log('失败 ' + fail + ' 项 / 共 ' + (pass + fail) + ' 项');
  process.exit(1);
})().catch(function (error) {
  console.log('FAIL  夹具自身异常：' + (error && error.stack ? error.stack : error));
  process.exit(1);
});
