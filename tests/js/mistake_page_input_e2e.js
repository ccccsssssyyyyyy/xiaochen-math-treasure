/**
 * 错题页「输入页号跳转 + 记住停留页」的**真实 DOM + 真实 HTTP** 端到端。
 *
 * 与 `mistake_page_input_check.js` 的分工：
 *   - 那个用假 DOM，跑得快、不依赖服务，断言状态与 localStorage 写了什么；
 *   - 这个把**真实的 index.html 与真实的 mistake.js** 装进 jsdom，批次数据经真实
 *     HTTP 取回，然后往真实输入框上派发真实键盘 / change 事件。
 *
 * 为什么要多这一层：页码输入框靠的是 HTML 属性上的 `onkeydown` / `onchange` 接线，
 * 假 DOM 里那一层是**手搓的桩**，压根没有「HTML 属性 → 全局函数」这条链路。属性拼错
 * 名字、函数忘了挂 window、jsdom 之外换成别的浏览器行为不同……都只有真跑才看得见。
 *
 * 前提：本地服务在跑。
 * 用法: node tests/js/mistake_page_input_e2e.js [baseUrl] [batchId]
 * 退出码: 0 全通过 / 1 有失败 / 2 环境不具备（jsdom 缺失 / 服务不可达 / 批次不足两页）
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

const BASE = (process.argv[2] || process.env.MISTAKE_PAGE_BASE || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const BATCH_ID = Number(process.argv[3] || process.env.MISTAKE_PAGE_BATCH || 1);
// 调试用：把某一支脚本换成磁盘上指定的那份（做反向对照 —— 拿改动前的 mistake.js
// 跑一遍，确认这个夹具确实会红）。
const OVERRIDE_JS_DIR = process.env.MISTAKE_PAGE_JS_DIR || '';

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

async function waitFor(probe, label, rounds) {
  for (let i = 0; i < (rounds || 200); i += 1) {
    if (probe()) return true;
    await tick();
  }
  console.log('  WARN  等待「' + label + '」超时，后面的断言会因此失真');
  return false;
}

(async function main() {
  let html;
  try {
    html = await get('/static/index.html');
  } catch (error) {
    console.log('SKIP  本地服务不可达：' + BASE + '（' + error.message + '）');
    process.exit(2);
  }

  // runScripts 必须是 'dangerously'：'outside-only' 不执行动态插入的 <script>，
  // 注入完页面一片安静、脚本其实一行没跑。不传 resources → 不拉 CDN 资源，
  // index.html 里那段 tailwind 内联配置会报一次 ReferenceError，与本次改动无关。
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', function (error) {
    console.log('  WARN  jsdom: ' + String(error && error.message).slice(0, 160));
  });
  const dom = new JSDOM(html, {
    url: BASE + '/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: virtualConsole,
  });
  const win = dom.window;
  const doc = win.document;

  win.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

  const requests = [];
  win.fetch = function (input, init) {
    const raw = typeof input === 'string' ? input : String(input && input.url ? input.url : input);
    const url = raw.indexOf('http') === 0 ? raw : BASE + raw;
    requests.push({ url: url, method: ((init && init.method) || 'GET').toUpperCase() });
    return fetch(url, init);
  };

  for (const name of ['math-render.js', 'mistake.js']) {
    const override = OVERRIDE_JS_DIR ? path.join(OVERRIDE_JS_DIR, name) : '';
    const source = override && fs.existsSync(override)
      ? fs.readFileSync(override, 'utf8')
      : await get('/static/js/' + name);
    const node = doc.createElement('script');
    node.textContent = source;
    doc.body.appendChild(node);
  }
  check('真实 index.html + 真实 mistake.js 已装进 jsdom',
    !!win.MistakeStore && typeof win.openMistakeBatch === 'function');

  const store = win.MistakeStore;
  const input = doc.getElementById('mistakePageInput');
  const totalEl = doc.getElementById('mistakePageTotal');
  const image = doc.getElementById('mistakePageImage');

  check('真实 DOM 里有页码输入框与总页数', !!input && !!totalEl);
  check('提交函数已挂到 window（内联属性才找得到它）',
    typeof win.mistakePageCommit === 'function' && typeof win.mistakePageInputKey === 'function');
  check('输入框上的接线是 HTML 属性（不是 JS 事后 bind，属性丢了就是静默失效）',
    /mistakePageCommit/.test(input.getAttribute('onchange') || '')
    && /mistakePageInputKey/.test(input.getAttribute('onkeydown') || ''),
    (input.getAttribute('onchange') || '(空)') + ' | ' + (input.getAttribute('onkeydown') || '(空)'));

  win.openMistakeBatch(BATCH_ID);
  const loaded = await waitFor(function () {
    return store.pages && store.pages.length > 1;
  }, '批次 ' + BATCH_ID + ' 载入');
  if (!loaded) { console.log('失败 ' + (fail + 1) + ' 项 / 共 ' + (pass + fail + 1) + ' 项'); process.exit(1); }

  const total = store.pages.length;
  console.log('  批次 ' + BATCH_ID + '：' + total + ' 页，当前停在第 ' + (store.pageIndex + 1) + ' 页');

  async function typeAndEnter(value) {
    input.value = value;
    input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    await tick();
    await tick();
    return tick();
  }

  /** 页图 src 是不是这一页的图（jsdom 可能把相对地址规范化，两头都认）。 */
  function imageShows(page) {
    const attr = image.getAttribute('src') || '';
    const resolved = String(image.src || '');
    return attr === page.url || resolved.indexOf(page.url) >= 0;
  }

  check('输入框初值＝当前页号，总页数写法是「/ N」',
    input.value === String(store.pageIndex + 1) && totalEl.textContent === '/ ' + total,
    input.value + ' ' + totalEl.textContent);

  // ---------------- 输入跳转
  const targetNo = total >= 4 ? Math.ceil(total / 2) : total;
  const beforeIndex = store.pageIndex;
  if (targetNo - 1 === beforeIndex) {
    console.log('SKIP  目标页恰好是当前页，跳过跳转断言');
  } else {
    await typeAndEnter(String(targetNo));
    check('回车后跳到第 ' + targetNo + ' 页',
      store.pageIndex === targetNo - 1, String(store.pageIndex));
    check('页图真的换成那一页的图（DOM 层证据，不是只看状态变量）',
      imageShows(store.pages[targetNo - 1]),
      (image.getAttribute('src') || '') + ' 期望 ' + store.pages[targetNo - 1].url);
    check('输入框显示收拢后的页号', input.value === String(targetNo), input.value);
  }

  // ---------------- 越界与非法输入
  await typeAndEnter('999');
  check('输入 999 → 收到最后一页', store.pageIndex === total - 1, String(store.pageIndex));
  check('页图跟到最后一页', imageShows(store.pages[total - 1]));
  await typeAndEnter('0');
  check('输入 0 → 落到第 1 页', store.pageIndex === 0, String(store.pageIndex));
  await typeAndEnter('随便写点啥');
  check('输入非数字 → 页不动、输入框还原成当前页号',
    store.pageIndex === 0 && input.value === '1', store.pageIndex + ' / ' + input.value);

  // ---------------- 停留页：退出再进
  const stayNo = total >= 3 ? total - 1 : total;
  if (stayNo > 1) {
    await typeAndEnter(String(stayNo));
    check('先停到第 ' + stayNo + ' 页', store.pageIndex === stayNo - 1, String(store.pageIndex));
    const saved = win.localStorage.getItem('mathbank_mistake_page_' + BATCH_ID);
    check('停留页写进了 localStorage（键按批次分）', saved === String(stayNo - 1), String(saved));

    // 模拟退出（回列表）再重新点进来
    win.backToMistakeList();
    store.pageIndex = 0;
    win.openMistakeBatch(BATCH_ID);
    await waitFor(function () { return store.pages.length > 1; }, '重进批次');
    await tick();
    check('重进同一批次 → 回到第 ' + stayNo + ' 页，而不是第 1 页',
      store.pageIndex === stayNo - 1, String(store.pageIndex));
    check('页图也跟着落在那一页', imageShows(store.pages[store.pageIndex]));
    check('输入框显示的是恢复后的页号', input.value === String(store.pageIndex + 1), input.value);

    // 换一个批次进去，不该串到上一个批次的页码
    const otherId = BATCH_ID + 1;
    store.pageIndex = 0;
    win.openMistakeBatch(otherId);
    await tick();
    await tick();
    check('另一个批次不会读到批次 ' + BATCH_ID + ' 的记录（各记各的）',
      win.localStorage.getItem('mathbank_mistake_page_' + otherId) === null,
      String(win.localStorage.getItem('mathbank_mistake_page_' + otherId)));
  }

  const writes = requests.filter(function (r) { return r.method !== 'GET'; });
  check('全程零写请求（翻页与输入都不改用户数据）',
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
