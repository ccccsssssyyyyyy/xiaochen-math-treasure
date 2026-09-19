/**
 * 错题工作台「合并题在页图上的几何」校验 —— 真实执行 mistake.js，不靠正则猜源码。
 *
 * 这一层原来的错法很安静：同页合并题记录的坐标是各成员块的**并集包围盒**，而成员
 * 矩形另存在 `page.manual_merges[].rects`。成员离得远（对角分布）时并集能撑成整页，
 * 于是点一下右侧卡片，左边那圈琥珀选中环看起来像「整页被选中」；染色与点击命中区
 * 也跟着铺满无关内容。实测批次1 的 11 组合并里有 4 组并集面积 0.80–0.89。
 *
 * 这里用假 DOM 把 mistake.js 整个加载进 vm 沙箱，喂进**真实坐标**（page2 的
 * m60735815：左下 [0,0.895254,0.4975,0.963232] + 右上 [0.4975,0.06926,1,0.265071]），
 * 直接断言渲染出来的块矩形、选中环的归属、成员序号、点击命中与框切割残段。
 *
 * 反向对照：`node tests/js/mistake_merge_rects_check.js .merge-rect-backup-20260918/mistake.js.bak`
 * —— 必须看到 [1] 段成片变红，否则这套断言等于没测。
 *
 * 用法: node tests/js/mistake_merge_rects_check.js [mistake.js 路径]
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

// ---------------------------------------------------------------- 真实数据

/** 截图那一组：左下 + 右上，对角分布，并集正好整页。 */
const MERGED_ID = 'm60735815';
const MEMBER_RECTS = [
  [0, 0.895254, 0.4975, 0.963232],
  [0.4975, 0.06926, 1, 0.265071]
];
/** 跨页组：成员各在自己页上，记录坐标本就是自己那一半。 */
const CROSS_ID = 'me5d1e279';

// ---------------------------------------------------------------- 假 DOM

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

const requests = [];
const documentHandlers = {};

function jsonResponse(ok, status, data) {
  return Promise.resolve({ ok: ok, status: status, json: function () { return Promise.resolve(data); } });
}

const sandbox = {
  console: console,
  setTimeout: function () { return 1; },
  clearTimeout: function () {},
  setInterval: function () { return 0; },
  clearInterval: function () {},
  fetch: function (url, options) {
    requests.push({ url: url, options: options || {} });
    return jsonResponse(true, 200, { status: 'success' });
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
vm.runInContext(src, sandbox, { filename: 'mistake.js' });

const store = sandbox.MistakeStore;
const overlay = elementFor('mistakePageOverlay');

// ---------------------------------------------------------------- HTML 解析

/** 把内联 style 拆成属性表 —— 必须按 `;` 切再比对名，否则 `top` 会撞上 `border-top`。 */
function styleMap(style) {
  const map = {};
  String(style || '').split(';').forEach(function (pair) {
    const i = pair.indexOf(':');
    if (i < 0) return;
    map[pair.slice(0, i).trim()] = pair.slice(i + 1).trim();
  });
  return map;
}

/** 页图上渲染出来的块（按出现顺序，带在 HTML 里的字符偏移）。 */
function pageBlocks() {
  const out = [];
  const re = /<div class="([^"]*)" data-record="(\d+)" style="([^"]*)"/g;
  let m;
  while ((m = re.exec(overlay.innerHTML)) !== null) {
    const style = styleMap(m[3]);
    out.push({
      record: Number(m[2]),
      className: m[1],
      offset: m.index,
      left: parseFloat(style.left),
      width: parseFloat(style.width),
      top: parseFloat(style.top),
      height: parseFloat(style.height),
      background: style.background || '',
      boxShadow: style['box-shadow'] || '',
      title: (/title="([^"]*)"/.exec(m[0]) || ['', ''])[1]
    });
  }
  return out;
}

function blocksOf(recordId) {
  return pageBlocks().filter(function (b) { return b.record === recordId; });
}

function near(a, b, tol) {
  return Math.abs(Number(a) - Number(b)) <= (tol === undefined ? 1e-6 : tol);
}

function rectOf(block) {
  return { left: block.left, width: block.width, top: block.top, height: block.height };
}

function fmt(rect) {
  return 'left=' + rect.left + ' width=' + rect.width + ' top=' + rect.top + ' height=' + rect.height;
}

/** 页图上出现的成员序号角标（紫圈，带「第 N 块」title）。 */
function memberChips(recordId) {
  const out = [];
  const re = new RegExp('<div class="absolute[^"]*" data-record="' + recordId + '"[\\s\\S]*?(?=<div class="absolute[^"]*" data-record=|$)', 'g');
  let m;
  while ((m = re.exec(overlay.innerHTML)) !== null) {
    const order = /title="这道合并题的第 (\d+) 块（共 (\d+) 块）"/.exec(m[0]);
    if (order) out.push({ index: Number(order[1]), total: Number(order[2]) });
  }
  return out;
}

/** 页图上「当前 · 块N」药丸的个数。 */
function currentPills() {
  return (overlay.innerHTML.match(/当前 · 块\d+/g) || []).length;
}

// ---------------------------------------------------------------- 状态

function render() {
  elementFor('mistakeWorkspaceSection').classList.remove('hidden');
  elementFor('mistakeDetailView').classList.remove('hidden');
  sandbox.setMistakeLayout('auto');
  sandbox.setMistakeCardFilter('all');
}

function clickCard(recordId, meta) {
  sandbox.handleMistakeCardClick({
    target: { closest: function () { return null; } },
    metaKey: !!meta, ctrlKey: false, preventDefault: function () {}
  }, recordId);
}

function clickBlock(recordId, meta) {
  (overlay.handlers.click || []).forEach(function (fn) {
    fn({
      target: {
        closest: function (selector) {
          return selector === '[data-record]'
            ? { dataset: { record: String(recordId) } } : null;
        }
      },
      metaKey: !!meta, ctrlKey: false, preventDefault: function () {}
    });
  });
}

function reset() {
  requests.length = 0;
  store.batch = { id: 7, subject: 'physics', title: '测试卷' };
  store.pageIndex = 0;
  store.pages = [{
    page_no: 1, url: '/page_1.png', width: 1000, height: 2000,
    layout: { mode: 'double', boundary: 0.4975, source: 'text_layer', confidence: 1 },
    column_boundary: 0.4975, snap_points: [], gap_candidates: [],
    column_snap_points: {}, column_gap_candidates: {},
    manual_boxes: [],
    manual_merges: [{ id: MERGED_ID, rects: MEMBER_RECTS, direction: 'v', primary: 0 }]
  }];
  store.records = [
    // 普通块：坐标就是自己，用于验「非合并记录零变化」
    {
      id: 11, page_no: 1, block_index: 0, column_index: 1,
      block_x_start: 0, block_x_end: 0.4975, block_y_start: 0.36, block_y_end: 0.52,
      grad_status: 'incorrect', merge_id: '', merged_block_count: 1, block_images: []
    },
    // 合并题：记录坐标是并集（整页），成员矩形另有出处
    {
      id: 900, page_no: 1, block_index: 11, column_index: 0,
      block_x_start: 0, block_x_end: 1, block_y_start: 0.06926, block_y_end: 0.963232,
      grad_status: 'incorrect', merge_id: MERGED_ID, merged_block_count: 2, block_images: []
    },
    // 跨页合并题：成员在别的页上，记录坐标＝本页那半
    {
      id: 901, page_no: 1, block_index: 5, column_index: 1,
      block_x_start: 0.005454, block_x_end: 0.90256,
      block_y_start: 0.891086, block_y_end: 0.917022,
      grad_status: 'unknown', merge_id: CROSS_ID, merged_block_count: 2, block_images: []
    }
  ];
  store.crossPageMerges = [{
    id: CROSS_ID, direction: 'v', primary: 0,
    members: [
      { page_no: 1, rect: [0.005454, 0.891086, 0.90256, 0.917022] },
      { page_no: 2, rect: [0.020394, 0.039022, 0.958088, 0.144264] }
    ]
  }];
  store.questionTypes = [];
  store.reasons = [];
  store.selectedRecordId = null;
  store.mergePick = [];
  store.mergePending = null;
  store.undoStack = [];
  store.boxMode = false;
  store.manualBoxes = {};
  store.boxDirty = {};
  store.boxSelected = null;
  store.pageLayouts = {};
  store.layoutDirty = {};
  store.pageFlashId = null;
  render();
}

// ---------------------------------------------------------------- [1] 未选中

console.log('[1] 合并题按成员矩形渲染（未选中）');
reset();

const mergedBlocks = blocksOf(900);
check('合并题在页图上渲染成 2 个块（不是 1 个并集块）', mergedBlocks.length === 2,
  '实际 ' + mergedBlocks.length + ' 个: ' + mergedBlocks.map(fmt).join(' | '));
check('第一个成员 = 左下那块（left 0 / width 49.75）',
  mergedBlocks[0] && near(mergedBlocks[0].left, 0) && near(mergedBlocks[0].width, 49.75, 1e-9),
  mergedBlocks[0] ? fmt(mergedBlocks[0]) : '缺块');
check('第一个成员 top/height = 89.5254 / 6.7978',
  mergedBlocks[0] && near(mergedBlocks[0].top, 89.5254) && near(mergedBlocks[0].height, 6.7978),
  mergedBlocks[0] ? fmt(mergedBlocks[0]) : '缺块');
check('第二个成员 = 右上那块（left 49.75 / width 50.25）',
  mergedBlocks[1] && near(mergedBlocks[1].left, 49.75) && near(mergedBlocks[1].width, 50.25, 1e-9),
  mergedBlocks[1] ? fmt(mergedBlocks[1]) : '缺块');
check('第二个成员 top/height = 6.926 / 19.5811',
  mergedBlocks[1] && near(mergedBlocks[1].top, 6.926) && near(mergedBlocks[1].height, 19.5811),
  mergedBlocks[1] ? fmt(mergedBlocks[1]) : '缺块');

const widest = pageBlocks().reduce(function (n, b) { return Math.max(n, b.width); }, 0);
check('页图上没有任何「铺满整页宽」的块（旧的并集块已消失）', widest < 99,
  '最宽的块 width=' + widest + '%');

const plain = blocksOf(11);
check('非合并记录仍渲染成 1 个块', plain.length === 1,
  '实际 ' + plain.length + ' 个');
check('非合并记录的坐标与自身字段一致（零变化）',
  plain[0] && near(plain[0].left, 0) && near(plain[0].width, 49.75, 1e-9)
    && near(plain[0].top, 36) && near(plain[0].height, 16),
  plain[0] ? fmt(plain[0]) : '缺块');

check('未选中时两个成员都不带琥珀选中环',
  mergedBlocks.length === 2 && mergedBlocks.every(function (b) { return b.boxShadow === ''; }),
  mergedBlocks.map(function (b) { return b.boxShadow; }).join(' | '));

// ---------------------------------------------------------------- [2] 选中态

console.log('');
console.log('[2] 选中态：环套在每个成员上，而不是并集上');
clickCard(900);

const selectedMerged = blocksOf(900);
check('选中后合并题仍是 2 个块', selectedMerged.length === 2,
  '实际 ' + selectedMerged.length + ' 个');
check('两个成员各自带琥珀选中环',
  selectedMerged.length === 2 && selectedMerged.every(function (b) {
    return b.boxShadow.indexOf('0 0 0 3px #f59e0b') >= 0;
  }),
  selectedMerged.map(function (b) { return b.boxShadow || '(空)'; }).join(' | '));
check('选中后仍然没有任何铺满整页宽的块', pageBlocks().every(function (b) { return b.width < 99; }),
  pageBlocks().map(function (b) { return b.record + ':' + b.width; }).join(' | '));
check('「当前 · 块N」药丸只有 1 个（每个成员都挂就分不清了）', currentPills() === 1,
  '实际 ' + currentPills() + ' 个');

// 光数个数不够：药丸必须落在**第一个成员**的 div 里，落到第二个上同样是错的
const pillOffset = overlay.innerHTML.indexOf('当前 · 块');
check('药丸挂在第一个成员上（位置在成员① 与成员② 之间）',
  selectedMerged.length === 2
    && pillOffset > selectedMerged[0].offset && pillOffset < selectedMerged[1].offset,
  'pill@' + pillOffset + ' 成员①@' + (selectedMerged[0] || {}).offset
    + ' 成员②@' + (selectedMerged[1] || {}).offset);

const chips = memberChips(900);
check('两个成员各有一个成员序号角标', chips.length === 2,
  '实际 ' + chips.length + ' 个: ' + JSON.stringify(chips));
check('序号按 rects 顺序 = 1 与 2，且都注明「共 2 块」',
  chips.length === 2 && chips[0].index === 1 && chips[1].index === 2
    && chips[0].total === 2 && chips[1].total === 2,
  JSON.stringify(chips));

const plainWhileMergedSelected = blocksOf(11);
check('选合并题时，页上的普通块不跟着亮（环只属于被选中的那条记录）',
  plainWhileMergedSelected.length === 1 && plainWhileMergedSelected[0].boxShadow === '',
  plainWhileMergedSelected.map(function (b) { return b.boxShadow || '(空)'; }).join(' | '));

// 普通块自身选中：环仍在它自己身上
clickCard(11);
const plainSelected = blocksOf(11);
check('普通块被选中：仍 1 个块 + 1 个环',
  plainSelected.length === 1 && plainSelected[0].boxShadow.indexOf('0 0 0 3px #f59e0b') >= 0,
  plainSelected.map(function (b) { return fmt(b) + ' / ' + b.boxShadow; }).join(' | '));
check('普通块被选中：不挂成员序号角标', memberChips(11).length === 0);

// ---------------------------------------------------------------- [3] 跨页组

console.log('');
console.log('[3] 跨页合并组不受影响（它本来就是成员模型）');
reset();
const crossBlocks = blocksOf(901);
check('跨页组仍渲染成 1 个块', crossBlocks.length === 1,
  '实际 ' + crossBlocks.length + ' 个: ' + crossBlocks.map(fmt).join(' | '));
check('跨页块的坐标＝记录自身在本页那半（宽度 89.7106%）',
  crossBlocks[0] && near(crossBlocks[0].left, 0.5454) && near(crossBlocks[0].width, 89.7106, 1e-4),
  crossBlocks[0] ? fmt(crossBlocks[0]) : '缺块');
clickCard(901);
const crossSelected = blocksOf(901);
check('跨页组选中后仍是 1 个块 + 1 个环', crossSelected.length === 1
  && crossSelected[0].boxShadow.indexOf('0 0 0 3px #f59e0b') >= 0,
  crossSelected.map(function (b) { return fmt(b); }).join(' | '));
check('跨页组不挂成员序号角标（角标只属于同页合并）', memberChips(901).length === 0);

// ---------------------------------------------------------------- [4] 框切割残段

console.log('');
console.log('[4] 人工框切割：残段按成员各算');
reset();
// 一只压住「右上那个成员」的框：只该切掉它，左下那个成员分毫不动
store.manualBoxes = {
  1: [[0.5, 0.10, 1.0, 0.20]]
};
render();
const cutBlocks = blocksOf(900);
check('被框压住的成员被切成 2 段', cutBlocks.length === 3,
  '实际 ' + cutBlocks.length + ' 个: ' + cutBlocks.map(fmt).join(' | '));
const untouched = cutBlocks.filter(function (b) { return near(b.top, 89.5254) && near(b.height, 6.7978); });
check('没被框压到的成员保持原样（1 段、尺寸不变）', untouched.length === 1,
  cutBlocks.map(fmt).join(' | '));
const cutParts = cutBlocks.filter(function (b) { return !near(b.top, 89.5254); });
check('被切成员的残段都在它自己的范围内（6.926 ~ 26.5071）',
  cutParts.length === 2 && cutParts.every(function (b) {
    return b.top >= 6.926 - 1e-6 && (b.top + b.height) <= 26.5071 + 1e-6;
  }),
  cutParts.map(fmt).join(' | '));

// ---------------------------------------------------------------- [5] 点击命中

console.log('');
console.log('[5] 点击命中区 = 成员块，不是并集');
reset();
check('每个成员块各自挂着 data-record（两个可点区域）', blocksOf(900).length === 2,
  '实际 ' + blocksOf(900).length + ' 个');
clickBlock(900);
check('点成员块能选中这条合并记录', store.selectedRecordId === 900,
  'selectedRecordId=' + store.selectedRecordId);
clickBlock(900);
check('再点一次仍是同一条记录（两条命中区指向同一个 id）', store.selectedRecordId === 900,
  'selectedRecordId=' + store.selectedRecordId);

// 命中面积：旧的并集块几乎盖住整页，现在只剩两个成员本身
const covered = blocksOf(900).reduce(function (s, b) { return s + (b.width / 100) * (b.height / 100); }, 0);
check('染色/命中面积从 89.4% 降到 13.2%（不再盖住无关内容）',
  near(covered * 100, 13.2204, 0.01),
  '实际 ' + (covered * 100).toFixed(4) + '%');

// ---------------------------------------------------------------- 收尾

console.log('');
console.log('通过 ' + pass + ' 项，失败 ' + fail + ' 项');
process.exit(fail ? 1 : 0);
