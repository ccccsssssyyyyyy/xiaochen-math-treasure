/**
 * 错题重做复习面板（redo.js）的假 DOM 运行期检查。
 *
 * 用法：node redo_payload_check.js <redo.js 路径>
 *
 * 为什么要有这个夹具：redo.js 是「点一下就对错落库」的界面，静态断言只能证明
 * 函数名和字段名写对了，证明不了**点的是哪道题、发出去的 body 长什么样、渲染出来
 * 的是不是导出印在纸上的那个版本**。这一层的错法全是静默的。
 *
 * 覆盖三条最容易翻车的链路：
 *   1. 录入：index.html 里静态的「做对/做错」按钮（NOT 动态卡片 —— 卡片只管题面）
 *      → POST /api/redo/papers/{id}/grade 的 results 必须是当前选中那一题；
 *      改判、自动前进、进度、已掌握提示都要跟上。
 *   2. 判读口径：卡片必须渲染后端给的 display_content（第 2 遍打乱过、第 3 遍没选项），
 *      而不是库里的原题面 —— 否则老师对着纸面点对错会点错题。
 *   3. 人工标注掌握度：按钮必须**钉在卡片这一道题上**（不能用「当前选中项」——
 *      请求往返期间用户切了题就会标错对象），且标注后的状态只能采信服务端返回的
 *      重放结果，不能在本地猜（标注可能被更晚的录入推翻，本地算不出来）。
 *   4. 题面里的图：错题补的图是写成 markdown 内联在正文里的
 *      （`![插图](/static/uploads/...)`），卡片必须把它渲染成 <img>，不能把 markdown
 *      原样印出来；正文与 image_paths 指向同一个文件时只出一张。
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const target = process.argv[2];
if (!target) {
    console.error('用法: node redo_payload_check.js <redo.js 路径>');
    process.exit(2);
}

let failures = 0;
let total = 0;
function check(label, ok, detail) {
    total += 1;
    if (ok) {
        console.log('PASS  ' + label);
    } else {
        failures += 1;
        console.log('FAIL  ' + label + (detail !== undefined ? '  — ' + detail : ''));
    }
    return !!ok;
}

// 夹具少实现一个方法（典型：classList.toggle）会在 promise 链里抛 TypeError，
// 而链上的 .then 被跳过、异常被吞掉 —— 表现成「前半段全过、后半段悄悄消失」。
process.on('unhandledRejection', (err) => {
    failures += 1;
    console.log('FAIL  未捕获的 rejection: ' + (err && err.stack ? err.stack : err));
});

const tick = () => new Promise((resolve) => setImmediate(resolve));

// ---------------------------------------------------------------- 假 DOM

const elementIds = [
    'mistakeListView', 'mistakeRedoView', 'mistakeDetailView', 'mistakeReviewView',
    'mistakeSubtabScan', 'mistakeSubtabRedo',
    'mistakeRedoDot', 'redoSubSummary', 'redoStatBar', 'redoSyncBtn', 'redoSyncBtnText',
    'redoBoardBtn', 'redoBoardPanel', 'redoQuickBtn', 'redoNewBtn', 'redoNewPanel',
    'redoScopeSelect', 'redoSubjectSelect', 'redoLimitInput', 'redoTitleInput',
    'redoNewHint', 'redoTaskList', 'redoTaskCount', 'redoGradeEmpty', 'redoGradePane',
    'redoGradeTitle', 'redoGradeMeta', 'redoHideAnswerToggle', 'redoGradeProgress',
    'redoGradeCard'
];

const elements = {};
function makeElement(id) {
    const classes = new Set();
    const element = {
        id: id,
        innerHTML: '',
        textContent: '',
        value: '',
        disabled: false,
        checked: false,
        scrollTop: 0,
        dataset: {},
        style: {},
        handlers: {},
        classList: {
            add() { for (const name of arguments) classes.add(name); },
            remove() { for (const name of arguments) classes.delete(name); },
            toggle(name, force) {
                const on = force === undefined ? !classes.has(name) : !!force;
                if (on) classes.add(name); else classes.delete(name);
                return on;
            },
            contains(name) { return classes.has(name); }
        },
        setAttribute(key, value) { this.dataset['attr_' + key] = String(value); },
        getAttribute(key) { return this.dataset['attr_' + key]; },
        removeAttribute(key) { delete this.dataset['attr_' + key]; },
        addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); },
        removeEventListener(type, fn) {
            const list = this.handlers[type] || [];
            const index = list.indexOf(fn);
            if (index !== -1) list.splice(index, 1);
        },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        appendChild() {},
        removeChild() {},
        closest() { return null; },
        getBoundingClientRect() { return { left: 0, top: 0, width: 1000, height: 2000, right: 1000, bottom: 2000 }; }
    };
    return element;
}
elementIds.forEach((id) => { elements[id] = makeElement(id); });
// 重做面板默认是隐藏的（index.html 里带 hidden），夹具必须与静态 HTML 同源
elements.mistakeRedoView.classList.add('hidden');
elements.mistakeDetailView.classList.add('hidden');
elements.mistakeReviewView.classList.add('hidden');
elements.redoNewPanel.classList.add('hidden');
elements.redoBoardPanel.classList.add('hidden');
elements.redoGradePane.classList.add('hidden');
elements.redoHideAnswerToggle.checked = true;
elements.redoLimitInput.value = '12';

const documentHandlers = {};
const documentStub = {
    getElementById(id) { return elements[id] || null; },
    addEventListener(type, fn) { (documentHandlers[type] = documentHandlers[type] || []).push(fn); },
    removeEventListener(type, fn) {
        const list = documentHandlers[type] || [];
        const index = list.indexOf(fn);
        if (index !== -1) list.splice(index, 1);
    },
    createElement() { return makeElement('created'); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    body: makeElement('body'),
    documentElement: makeElement('html'),
    activeElement: null
};

// ---------------------------------------------------------------- 假接口

const requests = [];
const toasts = [];
const mathRenders = [];
const openedTabs = [];

const QUESTION_1 = {
    id: 101,
    question_type: 'single_choice',
    score: 5,
    attempt_no: 1,
    option_mode: 'plain',
    content: '库里的原题面（不该被渲染）',
    display_content: '已知 $f(x)=x^2$，则 $f(3)=$（　　）\n\\begin{choices}\n\\item 3\n\\item 6\n\\item 9\n\\item 0\n\\end{choices}',
    display_answer: '由 $f(3)=9$，故选 C.',
    image_paths: [],
    redo: { wrong_count: 2, mastery_status: 'pending', next_redo_due: '2026-09-26', correct_answer: 'C' },
    recorded: { result: 'correct', option_mode: 'plain' }
};
const QUESTION_2 = {
    id: 102,
    question_type: 'multi_choice',
    score: 5,
    attempt_no: 2,
    option_mode: 'shuffled',
    content: '库里的原题面（第 2 遍）',
    display_content: '下列说法正确的是（　　）\n\\begin{choices}\n\\item 乙\n\\item 丁\n\\item 甲\n\\item 丙\n\\end{choices}',
    display_answer: 'AD\r\n由定义知甲、丙正确。',
    image_paths: [],
    redo: { wrong_count: 3, mastery_status: 'redone', next_redo_due: '2026-10-10', correct_answer: 'AC' },
    recorded: null
};
const QUESTION_3 = {
    id: 103,
    question_type: 'detailed_answer',
    score: 10,
    attempt_no: 3,
    option_mode: 'no_option',
    content: '库里的原题面（第 3 遍）',
    display_content: '求 $x$ 的值：$2x+1=5$。',
    display_answer: '解得 $x=2$。',
    image_paths: [],
    redo: { wrong_count: 1, mastery_status: 'pending', next_redo_due: null, correct_answer: '' },
    recorded: null
};

const overviewPayload = {
    status: 'success',
    today: '2026-09-20',
    stats: {
        pool_total: 20, pending: 18, due_now: 5, mastered: 2, stuck: 3,
        redo_total: 11, wrong_total: 26, backfill_pending: 4, answer_backfill_pending: 7,
        mastered_manual: 2
    },
    knowledge: [
        { knowledge: '3.1 函数的概念', total: 6, wrong_total: 9, pending: 5 },
        { knowledge: '2.2 基本不等式', total: 3, wrong_total: 4, pending: 3 }
    ]
};

const tasksPayload = {
    status: 'success',
    tasks: [
        { id: 7, title: '高一上第3周错题练习', subtitle: '', paper_type: 'exam', created_at: '2026-09-19T10:00:00Z', question_count: 3, graded_count: 1, status: 'partial', redo_round: 2, scope: 'paper', subject: 'math', due_date: '2026-09-19', graded_at: null, wrong_total: 6, mastered_count: 0, adopted: true },
        { id: 8, title: '错题重做卷（第 1 轮）', subtitle: '', paper_type: 'exam', created_at: '2026-09-12T10:00:00Z', question_count: 8, graded_count: 8, status: 'done', redo_round: 1, scope: 'all', subject: 'math', due_date: '2026-09-26', graded_at: '2026-09-12T20:00:00', wrong_total: 9, mastered_count: 6 }
    ]
};

const paperSummary = {
    id: 7, title: '高一上第3周错题练习', subtitle: '第 2 轮重做 · 2026-09-19', paper_type: 'exam',
    created_at: '2026-09-19T10:00:00Z', question_count: 3, graded_count: 1, status: 'partial',
    redo_round: 2, scope: 'due', subject: 'math', due_date: '2026-09-19', graded_at: null,
    wrong_total: 6, mastered_count: 0
};

const detailPayload = (questions, paper) => ({
    status: 'success',
    paper: paper || paperSummary,
    questions: questions
});

const gradedPayload = detailPayload([
    QUESTION_1,
    Object.assign({}, QUESTION_2, {
        recorded: { result: 'correct', option_mode: 'shuffled' },
        redo: { wrong_count: 3, mastery_status: 'mastered', next_redo_due: null, correct_answer: 'AC' }
    }),
    QUESTION_3
], Object.assign({}, paperSummary, { graded_count: 2, status: 'partial' }));

function jsonResponse(body, status) {
    return {
        ok: (status || 200) < 400,
        status: status || 200,
        headers: { get() { return 'application/json'; } },
        json: async () => body,
        text: async () => JSON.stringify(body)
    };
}

function pdfResponse() {
    return {
        ok: true,
        status: 200,
        headers: {
            get(name) {
                const key = String(name).toLowerCase();
                if (key === 'content-type') return 'application/pdf';
                if (key === 'x-redo-filename') return encodeURIComponent('高一上第3周错题练习.pdf');
                return null;
            }
        },
        blob: async () => ({ size: 2048, type: 'application/pdf' }),
        json: async () => ({}),
        text: async () => ''
    };
}

/** 人工标注接口的返回。服务端给的是**重放后**的真实状态（标注可能已被更晚的
 *  录入推翻），所以夹具也要给一份「重放结果」而不是回显入参 —— 前端若偷懒本地
 *  猜状态，这里就会对不上。 */
function masteryPayload(questionId, mastered) {
    return {
        status: 'success',
        question_id: questionId,
        mastered: mastered,
        override_active: mastered,
        redo: {
            correct_answer: '',
            wrong_count: 3,
            redo_count: 0,
            // 取消标注后回落到**重放结果**（这道题第 2 遍做对过 = redone），不是 pending：
            // 若这里写成 pending，夹具就测不出「取消后状态确实回落到历史判定」。
            redo_correct_streak: mastered ? 0 : 1,
            mastery_status: mastered ? 'mastered' : 'redone',
            next_redo_due: mastered ? null : '2026-09-26',
            last_redo_at: null,
            mastery_override: mastered ? 'mastered' : '',
            mastery_override_at: mastered ? '2026-09-20T12:00:00Z' : null
        }
    };
}

async function fakeFetch(input, init) {
    const url = String(input);
    const options = init || {};
    requests.push({ url: url, method: String(options.method || 'GET').toUpperCase(), options: options });

    if (url === '/api/redo/overview') return jsonResponse(overviewPayload);
    if (url === '/api/redo/tasks') return jsonResponse(tasksPayload);
    if (url === '/api/redo/papers/7' || /^\/api\/redo\/papers\/7$/.test(url)) return jsonResponse(detailPayload([QUESTION_1, QUESTION_2, QUESTION_3]));
    if (url === '/api/redo/papers/7/grade') return jsonResponse(gradedPayload);
    if (url === '/api/redo/papers/7/export') return pdfResponse();
    if (url === '/api/redo/papers/7' && options.method === 'DELETE') return jsonResponse({ status: 'success' });
    if (url === '/api/redo/papers') return jsonResponse({ status: 'success', paper_id: 9, question_count: 3, redo_round: 2, total_score: 15, due_date: '2026-09-26', title: '新卷' });
    if (url === '/api/redo/pool/sync') return jsonResponse({ status: 'success', seeded: 4, answer_filled: 7 });
    const masteryMatch = /^\/api\/redo\/questions\/(\d+)\/mastery$/.exec(url);
    if (masteryMatch) {
        return jsonResponse(masteryPayload(Number(masteryMatch[1]), !!JSON.parse(options.body || '{}').mastered));
    }
    // 注：认领（/api/redo/{eligibility,adopt,decline}）不在这里 —— redo.js 已经不再
    // 碰它，入口搬到了组卷面板第四格（paper.js 的 adoptPaperAsRedo，由 paper 家族夹具覆盖）。
    return jsonResponse({ status: 'error', message: '夹具未覆盖的接口: ' + url }, 404);
}

// ---------------------------------------------------------------- 沙箱

const storage = {};
const sandbox = {
    console: console,
    setTimeout: () => 0,
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
    setImmediate: setImmediate,
    Promise: Promise,
    JSON: JSON,
    fetch: fakeFetch,
    URL: { createObjectURL: () => 'blob:fake/1' },
    localStorage: {
        getItem: (k) => (Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null),
        setItem: (k, v) => { storage[k] = String(v); },
        removeItem: (k) => { delete storage[k]; }
    },
    document: documentStub,
    systemMetadata: { subjects: [{ value: 'math', label: '数学' }, { value: 'physics', label: '物理' }] },
    // 注：`MathRender` 不放替身 —— 题面正文的渲染口径就住在 math-render.js 里，
    // 放一个只带 render 的假对象等于把被测逻辑整个绕过。下面改用真模块 + 探针。
    // 只放本模块真正用到的那几个出口，但**行为要对得上**：safeImageUrl 若退化成
    // 「有值就放行」，「非法 URL 原样留着不生成 img」那条分支就永远测不出来。
    MathBankSafe: {
        escapeText: (value) => String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#039;'),
        escapeAttribute: (value) => String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#039;').replace(/\r?\n/g, ' '),
        // 真实实现要求：同域 + /static/uploads/ 前缀 + 位图后缀。沙箱里没有真的
        // location，用字符串判定等价替代。
        safeImageUrl: (value) => {
            const raw = String(value == null ? '' : value).trim();
            if (!raw || raw.indexOf('\\') !== -1) return '';
            if (!/^\/static\/(uploads|test_uploads)\//.test(raw)) return '';
            if (!/\.(png|jpe?g|gif|webp)$/i.test(raw)) return '';
            return raw;
        }
    },
    showToast: (message, type) => { toasts.push({ message: message, type: type }); },
    selectWorkspace: function (id) { sandbox.__workspaceCalls = (sandbox.__workspaceCalls || []).concat([id]); },
    open: (url) => {
        const tab = {
            location: { href: url },
            closed: false,
            document: { write: (html) => { tab.document.lastWrite = html; }, lastWrite: '' },
            close() { tab.closed = true; }
        };
        openedTabs.push(tab);
        return tab;
    }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

// index.html 里 math-render.js 排在所有业务脚本之前，沙箱必须同一顺序。
require('./sandbox_base').loadBaseModules(sandbox);
// 真模块的 render 在没有 renderMathInElement 的沙箱里会静默返回 false，
// 「卡片渲染后触发 KaTeX」这条断言就观察不到了 —— 补个探针，只记录不改语义。
const realMathRender = sandbox.MathRender.render;
sandbox.MathRender.render = (container, mode) => {
    mathRenders.push(container && container.id);
    return realMathRender(container, mode);
};

const source = fs.readFileSync(target, 'utf8');
vm.runInContext(source, sandbox, { filename: path.basename(target) });

// 录入区（做对/做错/上一题/下一题、导出、隐藏答案）的按钮是**静态**写在
// static/index.html 里的，redo.js 只负责往里填卡片内容 —— 所以那批入口在这里
// 没有动态 HTML 可抓，必须回读真实的 index.html。否则夹具会误以为「页面上没有
// 这个按钮」，或者退化成只断言 `typeof fn === 'function'`（挡不住传错参数）。
const indexPath = path.join(path.dirname(target), '..', 'index.html');
const indexHtml = fs.readFileSync(indexPath, 'utf8');

const el = (id) => elements[id];
const lastRequest = () => requests[requests.length - 1];
const requestBody = (request) => JSON.parse(request.options.body);

/** 从 HTML（动态渲染的片段或静态 index.html）里把带 onclick 的标签抓出来并真的
 *  执行它 —— 内联处理器是字符串拼的，漏写不报错、点了没反应，只断言函数存在挡不住。 */
function clickRendered(html, pattern) {
    const tag = new RegExp(pattern).exec(html);
    if (!tag) return false;
    const handler = /onclick="([^"]*)"/.exec(tag[0]);
    if (!handler) return false;
    vm.runInContext(handler[1], sandbox);
    return true;
}

/** 只把内联 onclick 原文取出来（不执行）—— 用于断言「按钮传的是哪一道题」。
 *  注意 pattern 必须把闭合的 `"` 也匹配进去，否则 tag[0] 截断在半个属性上。 */
function onclickSource(html, pattern) {
    const tag = new RegExp(pattern).exec(html);
    if (!tag) return null;
    const handler = /onclick="([^"]*)"/.exec(tag[0]);
    return handler ? handler[1] : null;
}

async function main() {
    // ==================== 1. 模块出口 ====================
    for (const name of ['switchMistakeSubtab', 'closeRedoReview', 'openMistakeRedo', 'openRedoPaper',
        'redoStep', 'gradeRedoCurrent', 'setRedoHideAnswer', 'exportRedoPaper', 'deleteRedoPaper',
        'createQuickRedoPaper', 'submitRedoNewPaper', 'syncRedoPool', 'toggleRedoBoard',
        'toggleRedoNewPanel', 'cancelRedoDelete', 'markRedoMastery']) {
        check('导出 ' + name, typeof sandbox[name] === 'function');
    }

    // DOMContentLoaded：刷角标 + 填学科下拉
    (documentHandlers['DOMContentLoaded'] || []).forEach((fn) => fn({}));
    await tick();
    check('挂载时刷新「待重做」角标（GET overview）',
        requests.some((r) => r.url === '/api/redo/overview' && r.method === 'GET'));
    check('重做池有到期题时角标可见', !el('mistakeRedoDot').classList.contains('hidden'));
    check('学科下拉被填充', el('redoSubjectSelect').innerHTML.includes('数学') && el('redoSubjectSelect').innerHTML.includes('物理'));

    // ==================== 2. 子视图切换 ====================
    requests.length = 0;
    // 复现真实路径：用户停在「批次详情（切题页）」时被认领跳转拉过来。
    // 只藏 listView 的旧写法下 detailView 与 redoView 会同屏，两个 flex-1
    // 平分高度 → 重做复习页骨架吃掉 145px、题干区被压成 0（2026-09-20 用户截图）。
    el('mistakeDetailView').classList.remove('hidden');
    el('mistakeReviewView').classList.remove('hidden');
    sandbox.switchMistakeSubtab('redo');
    await tick(); await tick();
    check('切到重做面板后批次列表隐藏', el('mistakeListView').classList.contains('hidden'));
    check('切到重做面板后重做视图显示', !el('mistakeRedoView').classList.contains('hidden'));
    check('切到重做面板后批次详情/审校页一并收起（视图不叠加）',
        el('mistakeDetailView').classList.contains('hidden')
        && el('mistakeReviewView').classList.contains('hidden'));
    check('子标签高亮切到重做复习', el('mistakeSubtabRedo').classList.contains('active')
        && !el('mistakeSubtabScan').classList.contains('active'));
    check('子标签同步 aria-selected',
        el('mistakeSubtabRedo').getAttribute('aria-selected') === 'true'
        && el('mistakeSubtabScan').getAttribute('aria-selected') === 'false');
    check('进入面板即拉取总览与任务列表',
        requests.some((r) => r.url === '/api/redo/overview') && requests.some((r) => r.url === '/api/redo/tasks'));

    // ==================== 3. 统计条与任务列表渲染 ====================
    const statBar = el('redoStatBar').innerHTML;
    check('统计条渲染四项以上指标',
        statBar.includes('待重做') && statBar.includes('已到期') && statBar.includes('已掌握')
        && statBar.includes('反复错') && statBar.includes('累计重做次数'));
    check('统计条显示真实数字', statBar.includes('>18<') && statBar.includes('>5<'));
    // 「已掌握」里混着系统判的与老师标的两种来源，分开显示老师才能复核自己标过的那些
    check('统计条单独标出人工标注的已掌握数', statBar.includes('其中 2 道人工标注'));
    check('副标题显示重做池规模与待补录历史错题',
        el('redoSubSummary').textContent.includes('重做池 20 道')
        && el('redoSubSummary').textContent.includes('4 道历史错题未进池'));
    check('「同步历史错题」按钮按待补录数显示并带计数',
        !el('redoSyncBtn').classList.contains('hidden')
        && el('redoSyncBtnText').textContent.includes('4'));

    const taskHtml = el('redoTaskList').innerHTML;
    check('任务列表渲染两份重做卷（一张认领的、一张错题台生成的）',
        el('redoTaskCount').textContent === '2 份'
        && taskHtml.includes('高一上第3周错题练习') && taskHtml.includes('错题重做卷（第 1 轮）'));
    // 徽章只挂在认领的那张上，错题台自己生成的卷不该跟着标
    check('「组卷台认领」徽章只出现在认领的那张卡片上',
        (taskHtml.match(/组卷台认领/g) || []).length === 1
        && taskHtml.indexOf('组卷台认领') < taskHtml.indexOf('错题重做卷（第 1 轮）'));
    check('任务卡带状态徽章与轮次', taskHtml.includes('录入中') && taskHtml.includes('第 2 轮'));
    check('任务卡带进度条与录入进度', taskHtml.includes('1/3 已录入') && taskHtml.includes('width:33%'));
    check('到期日为今天或更早时高亮（text-brand-700）', taskHtml.includes('text-brand-700'));

    // ==================== 4. 打开重做卷：判读口径 ====================
    requests.length = 0;
    check('任务卡挂着真实打开入口（内联 onclick）',
        clickRendered(taskHtml, '<div class="min-w-0 cursor-pointer"[^>]*>'));
    await tick(); await tick();
    check('打开重做卷走 GET /api/redo/papers/7',
        lastRequest().url === '/api/redo/papers/7' && lastRequest().method === 'GET');
    check('切换到录入视图（空态隐藏）',
        !el('redoGradePane').classList.contains('hidden') && el('redoGradeEmpty').classList.contains('hidden'));
    check('标题与副信息来自接口',
        el('redoGradeTitle').textContent === '高一上第3周错题练习'
        && el('redoGradeMeta').textContent.includes('第 2 轮 · 3 题'));

    const card = el('redoGradeCard').innerHTML;
    check('默认停在第一道**未录入**的题上（第 2 题）', card.includes('第 2/3 题'));
    // latexPreviewHtml 会把 \begin{choices} 剥离后用 A./B./C./D. 重渲染，所以卡片里
    // 不该有 \item，而是要按**打乱后**的顺序出现 乙→丁→甲→丙（库里的原序是另一样）。
    check('渲染的是导出时的形态（打乱后的选项顺序）', (() => {
        const order = ['乙', '丁', '甲', '丙'].map((s) => card.indexOf(s));
        return order.every((i) => i >= 0)
            && order[0] < order[1] && order[1] < order[2] && order[2] < order[3]
            && card.includes('A.</b> 乙')
            && !card.includes('\\item') && !card.includes('\\begin{choices}');
    })(), card.slice(0, 220));
    check('不渲染库里的原题面', !card.includes('库里的原题面'));
    check('选项打乱档位有徽章与说明',
        card.includes('选项打乱') && card.includes('答案键同步重映射'));
    check('显示该题第几遍重做与累计错次',
        card.includes('第 2 遍重做') && card.includes('累计错 3 次'));
    check('默认隐藏答案', card.includes('答案已隐藏') && !card.includes('由定义知甲、丙正确。'));
    check('卡片渲染后触发 KaTeX', mathRenders.includes('redoGradeCard'));

    // 取消隐藏 → 答案出现
    sandbox.setRedoHideAnswer(false);
    const cardShown = el('redoGradeCard').innerHTML;
    check('取消隐藏后显示答案（含首行裸字母式答案原文）',
        !cardShown.includes('答案已隐藏') && cardShown.includes('由定义知甲、丙正确。'));
    check('答案区高亮【答案】标记逻辑不影响无标记答案', cardShown.includes('AD'));
    sandbox.setRedoHideAnswer(true);
    check('重新隐藏答案生效', el('redoGradeCard').innerHTML.includes('答案已隐藏'));

    // ==================== 5. 录入：请求体与状态推进 ====================
    requests.length = 0;
    check('页面上挂着真实「做对」入口（index.html 静态按钮）',
        clickRendered(indexHtml, '<button[^>]*onclick="gradeRedoCurrent\\(\'correct\'\\)"'));
    await tick(); await tick();
    const gradeRequest = requests.filter((r) => r.url === '/api/redo/papers/7/grade')[0];
    check('录入走 POST /api/redo/papers/7/grade', !!gradeRequest && gradeRequest.method === 'POST');
    const gradeBody = requestBody(gradeRequest);
    check('请求体只带当前这一题与对错', gradeBody.results.length === 1
        && gradeBody.results[0].question_id === 102 && gradeBody.results[0].result === 'correct',
        JSON.stringify(gradeBody));
    check('录入后自动前进下一题', el('redoGradeCard').innerHTML.includes('第 3/3 题'));
    check('录入后进度更新为 2/3', el('redoGradeProgress').innerHTML.includes('2/3'));
    check('已掌握的题给出明确提示',
        toasts.some((t) => t.message.includes('已掌握')));
    check('录入后刷新任务列表（角标同步）',
        requests.some((r) => r.url === '/api/redo/tasks') && requests.some((r) => r.url === '/api/redo/overview'));

    // 返回上一题：录完会自动前进，所以退一步回的是刚录的那道（第 2 题）
    sandbox.redoStep(-1);
    const backCard = el('redoGradeCard').innerHTML;
    check('上一题回显已录入结果', backCard.includes('已录：做对'));
    check('退一步回到第 2 题（第 2 遍 · 选项打乱）',
        backCard.includes('第 2/3 题') && backCard.includes('第 2 遍重做') && backCard.includes('选项打乱'),
        backCard.slice(0, 160));

    // 再退一步才是第 1 题（第 1 遍 · 原序）
    sandbox.redoStep(-1);
    const firstCard = el('redoGradeCard').innerHTML;
    check('第 1 遍题显示「原序」档位',
        firstCard.includes('原序') && firstCard.includes('第 1 遍重做'), firstCard.slice(0, 160));

    // 做错按钮的 payload（作用在当前选中的第 1 题上）
    requests.length = 0;
    check('页面上挂着真实「做错」入口（index.html 静态按钮）',
        clickRendered(indexHtml, '<button[^>]*onclick="gradeRedoCurrent\\(\'incorrect\'\\)"'));
    await tick(); await tick();
    const wrongBody = requestBody(requests.filter((r) => r.url === '/api/redo/papers/7/grade')[0]);
    check('做错送的是当前选中那一题（第 1 题）且 result=incorrect',
        wrongBody.results[0].result === 'incorrect'
        && wrongBody.results[0].question_id === 101, JSON.stringify(wrongBody));

    // ==================== 6. 出卷面板 ====================
    sandbox.toggleRedoNewPanel();
    check('生成面板展开', !el('redoNewPanel').classList.contains('hidden'));
    check('面板提示带当前可出题数',
        el('redoNewHint').textContent.includes('5 道已到期') && el('redoNewHint').textContent.includes('18 道未掌握'));

    requests.length = 0;
    el('redoScopeSelect').value = 'all';
    el('redoSubjectSelect').value = 'physics';
    el('redoLimitInput').value = '7';
    el('redoTitleInput').value = '期末专项';
    await sandbox.submitRedoNewPaper();
    await tick();
    const createBody = requestBody(requests.filter((r) => r.url === '/api/redo/papers')[0]);
    check('生成重做卷的 body 与控件值一致',
        createBody.scope === 'all' && createBody.subject === 'physics'
        && createBody.limit === 7 && createBody.title === '期末专项', JSON.stringify(createBody));
    check('生成后面板收起', el('redoNewPanel').classList.contains('hidden'));
    check('生成后自动打开新卷', requests.some((r) => r.url === '/api/redo/papers/9'));

    // 一键重点复习卷：题量按到期数取，封顶 30
    requests.length = 0;
    await sandbox.createQuickRedoPaper();
    await tick();
    const quickBody = requestBody(requests.filter((r) => r.url === '/api/redo/papers')[0]);
    check('重点复习卷只取已到期的题', quickBody.scope === 'due' && quickBody.limit === 5, JSON.stringify(quickBody));

    // ==================== 7. 同步历史错题 ====================
    requests.length = 0;
    toasts.length = 0;
    await sandbox.syncRedoPool();
    await tick();
    // 注意：同步完还会接着重拉总览与任务，所以不能用 lastRequest() 找 sync 本身。
    check('同步走 POST /api/redo/pool/sync',
        requests.some((r) => r.url === '/api/redo/pool/sync' && r.method === 'POST'));
    check('同步后重拉总览与任务列表（数字立刻归位）',
        requests.some((r) => r.url === '/api/redo/overview' && r.method === 'GET')
        && requests.some((r) => r.url === '/api/redo/tasks' && r.method === 'GET'));
    check('同步结果汇总进提示',
        toasts.some((t) => t.message.indexOf('补入 4 道历史错题') !== -1 && t.message.indexOf('补全 7 道题的答案字母') !== -1),
        JSON.stringify(toasts));

    // ==================== 8. 知识点看板 ====================
    sandbox.toggleRedoBoard();
    const board = el('redoBoardPanel').innerHTML;
    check('看板展开并渲染知识点', !el('redoBoardPanel').classList.contains('hidden')
        && board.includes('3.1 函数的概念') && board.includes('2.2 基本不等式'));
    check('看板带错次与未掌握数', board.includes('错 9 次 / 5 道未掌握'));
    sandbox.toggleRedoBoard();
    check('看板可再次收起', el('redoBoardPanel').classList.contains('hidden'));

    // ==================== 9. 导出 ====================
    requests.length = 0;
    openedTabs.length = 0;
    await sandbox.openRedoPaper(7);
    await tick();
    await sandbox.exportRedoPaper(true);
    await tick();
    const exportRequest = requests.filter((r) => r.url === '/api/redo/papers/7/export')[0];
    check('导出走 POST /api/redo/papers/7/export',
        !!exportRequest && exportRequest.method === 'POST');
    check('「答案版」带 include_answers=true 且 format=pdf',
        requestBody(exportRequest).include_answers === true && requestBody(exportRequest).format === 'pdf',
        JSON.stringify(requestBody(exportRequest)));
    check('导出前先开标签页（避免被拦截）', openedTabs.length === 1);
    check('PDF 在新标签页打开', openedTabs[0] && String(openedTabs[0].location.href).indexOf('blob:') === 0);
    check('导出成功给出提示', toasts.some((t) => t.message.indexOf('PDF 编译成功') !== -1));

    // ==================== 10. 删除：两段式确认 ====================
    requests.length = 0;
    sandbox.deleteRedoPaper(8, false);
    check('第一次点删除只进入确认态，不发请求', requests.length === 0
        && el('redoTaskList').innerHTML.includes('确认删除'));
    check('确认态按钮挂着真实入口',
        clickRendered(el('redoTaskList').innerHTML, '<button[^>]*onclick="deleteRedoPaper\\(8, true\\)"'));
    await tick();
    check('确认后发 DELETE /api/redo/papers/8',
        lastRequest().url === '/api/redo/papers/8' && lastRequest().method === 'DELETE');

    sandbox.deleteRedoPaper(8, false);
    check('取消后回到普通态', (() => {
        sandbox.cancelRedoDelete();
        return !el('redoTaskList').innerHTML.includes('确认删除');
    })());

    // ==================== 11. 工作台切换：回到错题台不停在上一轮录入页 ====================
    // selectWorkspace 的包装只在「切回 mistake」时收拾面板（见 redo.js 挂载段注释），
    // 所以要测的是：去别的台转一圈再回来，落点是批次列表而不是上次那道题。
    sandbox.switchMistakeSubtab('redo');
    await tick();
    check('准备：重做面板确实开着', !el('mistakeRedoView').classList.contains('hidden'));

    requests.length = 0;
    sandbox.selectWorkspace('bank', '题库工作台');
    sandbox.selectWorkspace('mistake', '错题工作台');
    await tick();
    check('回到错题台时收起重做面板、落回批次列表',
        el('mistakeRedoView').classList.contains('hidden')
        && !el('mistakeListView').classList.contains('hidden')
        && el('mistakeSubtabScan').classList.contains('active')
        && !el('mistakeSubtabRedo').classList.contains('active'));
    check('回到错题台时刷新「待重做」角标',
        requests.some((r) => r.url === '/api/redo/overview' && r.method === 'GET'));

    // 落点：重做录入页照旧收起，但其余三个视图**谁开着就落谁**。无条件打开列表的写法
    // 会在「停在批次详情页时切回来」这一步让列表与详情各拿一半高度（2026-09-20 实测：
    // 两个 flex-1 同屏，重做录入页的固定骨架吃掉 145px，题干区被压成 0）。
    el('mistakeListView').classList.add('hidden');
    el('mistakeDetailView').classList.remove('hidden');
    sandbox.selectWorkspace('bank', '题库工作台');
    sandbox.selectWorkspace('mistake', '错题工作台');
    await tick();
    check('停在批次详情页时切回：保留详情、不再把列表也打开',
        !el('mistakeDetailView').classList.contains('hidden')
        && el('mistakeListView').classList.contains('hidden')
        && el('mistakeRedoView').classList.contains('hidden'));

    el('mistakeDetailView').classList.add('hidden');
    el('mistakeReviewView').classList.remove('hidden');
    sandbox.selectWorkspace('bank', '题库工作台');
    sandbox.selectWorkspace('mistake', '错题工作台');
    await tick();
    check('停在错题本审校页时切回：保留审校页（未保存的草稿不能丢）',
        !el('mistakeReviewView').classList.contains('hidden')
        && el('mistakeListView').classList.contains('hidden'));

    el('mistakeReviewView').classList.add('hidden');
    sandbox.selectWorkspace('bank', '题库工作台');
    sandbox.selectWorkspace('mistake', '错题工作台');
    await tick();
    check('一个都没开时落回批次列表',
        !el('mistakeListView').classList.contains('hidden')
        && el('mistakeDetailView').classList.contains('hidden')
        && el('mistakeReviewView').classList.contains('hidden'));

    // ==================== 12. 人工标注「已掌握」 ====================
    // 标注不是直接写字段，而是插进重放序列的一条**事件**：服务端返回重放后的状态，
    // 前端只能采信它。按钮上挂的是卡片这一道题的 id，而不是「当前选中项」。
    const MASTERY_BTN = '<button[^>]*onclick="markRedoMastery\\([^"]*\\)"';

    sandbox.switchMistakeSubtab('redo');
    await tick();
    await sandbox.openRedoPaper(7);
    await tick(); await tick();
    const freshCard = el('redoGradeCard').innerHTML;
    check('准备：重新打开重做卷，停在第一道未录入题', freshCard.includes('第 2/3 题'));

    check('「标为已掌握」按钮钉在卡片这一道题上（传 id=102）',
        onclickSource(freshCard, MASTERY_BTN) === 'markRedoMastery(102, true)',
        String(onclickSource(freshCard, MASTERY_BTN)));
    check('未标注时按钮文案是「标为已掌握」、不挂「人工标注」徽章',
        freshCard.includes('标为已掌握') && !freshCard.includes('人工标注')
        && !freshCard.includes('取消掌握标注'));
    check('多选题提示「多选按全对计」（不接受部分对）', freshCard.includes('多选按全对计'));

    requests.length = 0;
    toasts.length = 0;
    check('卡片入口可点（内联 onclick 真的挂着）', clickRendered(freshCard, MASTERY_BTN));
    await tick(); await tick();
    const markReq = requests.filter((r) => r.url === '/api/redo/questions/102/mastery')[0];
    check('标注走 POST /api/redo/questions/102/mastery', !!markReq && markReq.method === 'POST');
    check('请求体只带 mastered=true',
        !!markReq && JSON.stringify(requestBody(markReq)) === '{"mastered":true}',
        markReq ? markReq.options.body : '(没有发出请求)');

    const markedCard = el('redoGradeCard').innerHTML;
    check('标注后挂「人工标注」徽章（区别于系统判定的已掌握）', markedCard.includes('人工标注'));
    check('标注后掌握度切到已掌握', markedCard.includes('已掌握') && !markedCard.includes('重做对过'));
    check('标注后不再排建议重做日（服务端清了 next_redo_due）', !markedCard.includes('建议 '));
    check('标注后按钮变「取消掌握标注」', markedCard.includes('取消掌握标注'));
    check('标注后有明确反馈', toasts.some((t) => t.message.includes('已标为掌握')));

    check('取消入口回传 mastered=false',
        onclickSource(markedCard, MASTERY_BTN) === 'markRedoMastery(102, false)',
        String(onclickSource(markedCard, MASTERY_BTN)));
    requests.length = 0;
    clickRendered(markedCard, MASTERY_BTN);
    await tick(); await tick();
    const unmarkReq = requests.filter((r) => r.url === '/api/redo/questions/102/mastery')[0];
    check('取消标注送 mastered=false',
        !!unmarkReq && JSON.parse(unmarkReq.options.body).mastered === false);
    const restoredCard = el('redoGradeCard').innerHTML;
    check('取消后徽章消失、按钮回到「标为已掌握」',
        !restoredCard.includes('人工标注') && restoredCard.includes('标为已掌握'));
    check('取消后建议重做日回来（状态回落到重放结果）',
        restoredCard.includes('重做对过') && restoredCard.includes('建议 2026-09-26'));

    // ==================== 13. 认领组卷台那张卷（重做台这一侧） ====================
    //
    // 断点：错题闭环的第 1 遍练习几乎必然发生在组卷台（入库即送组卷 → 排版 → 印出来），
    // 可那张卷不带 is_redo，它的做题结果没有任何入口写回 redo_attempts。
    //
    // 认领入口在**组卷面板第四格**（paper.js 的 adoptPaperAsRedo → POST /api/redo/adopt），
    // 不在这份夹具的加载范围里，由 paper 家族夹具与 Python 契约测试覆盖。这里只钉属于
    // 重做台的两件事：任务卡上的「组卷台认领」标记，以及认领卷里的非错题照样能录。

    check('任务卡上认领卷有「组卷台认领」标记',
        el('redoTaskList').innerHTML.includes('组卷台认领'));

    // 认领卷里的非错题：标出来，但照样能录（做错就进池）
    const notPooledQuestion = {
        id: 104,
        question_type: 'detailed_answer',
        score: 10,
        attempt_no: 1,
        option_mode: 'plain',
        content: '库里的原题面（非错题）',
        display_content: '求 $x$ 的值：$2x+1=5$。',
        display_answer: '解得 $x=2$。',
        image_paths: [],
        redo: { wrong_count: 0, mastery_status: 'pending', next_redo_due: null, correct_answer: '' },
        recorded: null
    };
    sandbox.RedoStore.questions = [notPooledQuestion, Object.assign({}, QUESTION_2)];
    sandbox.RedoStore.index = 1;
    sandbox.redoStep(-1);
    const notPooledCard = el('redoGradeCard').innerHTML;
    check('认领卷里的非错题标出「非错题 · 做错后进池」',
        notPooledCard.includes('非错题 · 做错后进池'));
    sandbox.RedoStore.index = 0;
    sandbox.redoStep(1);
    check('池内题不显示这个徽章',
        !el('redoGradeCard').innerHTML.includes('非错题 · 做错后进池'));

    // ==================== 14. 题面里的图：抽到选项前统一摆 ====================
    //
    // 2026-09-20 用户截图：题干里的图渲染成一串 `![插图](/static/uploads/...)` 文字。
    // 错题入库时补的图**就是**这么存的（写在正文里，见 mistake.js 的补图逻辑），
    // 而 redo.js 的题面渲染只认 choices/\fillin/\paren —— 于是同一道题在错题详情、
    // 组卷台、题库编辑器、导出的 PDF 上都有图，只有重做录入页没有。
    //
    // 库里 102 道题命中这种语法（题干或解析），其中 14 道 image_paths 是空的、
    // 图**只**能靠正文这条路渲染出来。所以这里必须真跑渲染，不能只断言函数名。
    //
    // 2026-09-21 再改一次**图放哪里**：原先屏幕上图留在正文出现的原位，而试卷 PDF
    // 把图抽出来摆到「题干之后、选项之前」（`paper_helper.py`：stem_text → 图 →
    // choices_part）。录入对错是**对着印出来的重做卷**判的，同一道题两个位置看着
    // 不像同一张卷。现在录入页也抽图、也插在选项之前、也居中 —— 与本节第 ⑥ 项
    // 断言（图在 A./B. 之前）以及 PDF 同口径。图只在正文里、image_paths 为空的那
    // 批题照样要出图，所以下面的用例继续覆盖「图只长在正文」这条路径。
    const FIGURE_A = '/static/uploads/mistakes/1/figures/crop_p015_7eb99e70c0.png';
    const FIGURE_B = '/static/uploads/mistakes/1/figures/crop_p015_5c9742c26d.png';
    const FIG_BLOCK_CLASS = 'flex flex-wrap justify-center gap-2 pt-1.5';
    const countOf = (haystack, needle) => haystack.split(needle).length - 1;
    const figureCard = () => el('redoGradeCard').innerHTML;
    // 题干气泡（含配图块）从题干文字 div 起、到卡片末尾的配图块结束；
    // 配图块里不嵌套 div，所以第一个 </div> 就是它的闭合。
    const figureBlockOf = (card) => {
        const start = card.indexOf(FIG_BLOCK_CLASS);
        if (start === -1) return '';
        const end = card.indexOf('</div>', start);
        return card.slice(start, end === -1 ? card.length : end);
    };
    const stemOf = (card) => {
        const start = card.indexOf('text-[13px] leading-relaxed');
        const blockAt = card.indexOf(FIG_BLOCK_CLASS, start);
        return card.slice(start, blockAt === -1 ? card.length : blockAt);
    };

    // ① 图只在正文里（那 14 道物理错题的形态）
    const inlineOnlyQuestion = {
        id: 201,
        question_type: 'single_choice',
        score: 5,
        attempt_no: 1,
        option_mode: 'plain',
        content: '库里的原题面（有图）',
        display_content: '如图所示，用频闪照相的方法记录某同学的运动情况的是\n\n![插图](' + FIGURE_A + ')',
        display_answer: '解析：由图可见答案选 C。',
        image_paths: [],
        redo: { wrong_count: 1, mastery_status: 'pending', next_redo_due: null, correct_answer: 'C' },
        recorded: null
    };
    sandbox.RedoStore.questions = [inlineOnlyQuestion];
    sandbox.RedoStore.index = 0;
    sandbox.setRedoHideAnswer(true);
    const inlineCard = figureCard();
    check('正文里的图渲染成 <img>（不再印 markdown 原文）',
        inlineCard.includes('<img src="' + FIGURE_A + '"'), inlineCard.slice(0, 320));
    check('正文里不再残留 ![插图](...) 字面量', !inlineCard.includes('![插图]'));
    check('图已从正文流里摘出来（题干文字段里不再直接嵌 <img>）',
        !stemOf(inlineCard).includes('<img'), stemOf(inlineCard).slice(0, 200));
    check('抽出来的图落在配图块里、居中（与 PDF 题末居中间口径）',
        figureBlockOf(inlineCard).includes('<img src="' + FIGURE_A + '"'),
        figureBlockOf(inlineCard).slice(0, 200));
    check('配图块与 image_paths 那条路同一尺寸（max-h-56 + 圆角描边）',
        inlineCard.includes('class="max-h-56 rounded-lg border border-slate-200 dark:border-slate-700"'));
    check('配图块的 img 带 alt（抽走后不再保留每条 markdown 的 alt）',
        figureBlockOf(inlineCard).includes('alt="题图"'));
    check('图文顺序不乱：题干文字在前、图在后', (() => {
        const stem = inlineCard.indexOf('频闪照相');
        const img = inlineCard.indexOf('<img src="' + FIGURE_A + '"');
        return stem >= 0 && img > stem;
    })());

    // ② 正文与 image_paths 指同一个文件（库里 68 道题是这种）→ 只能出一张
    const duplicatedQuestion = Object.assign({}, inlineOnlyQuestion, { image_paths: [FIGURE_A] });
    sandbox.RedoStore.questions = [duplicatedQuestion];
    sandbox.setRedoHideAnswer(true);
    check('正文与 image_paths 指同一文件时只渲染一张图',
        countOf(figureCard(), 'src="' + FIGURE_A + '"') === 1,
        '出现 ' + countOf(figureCard(), 'src="' + FIGURE_A + '"') + ' 次');

    // ③ image_paths 里另一张**不同**的图照常出（去重不能顺手把它也吞掉）
    const mixedQuestion = Object.assign({}, inlineOnlyQuestion, { image_paths: [FIGURE_A, FIGURE_B] });
    sandbox.RedoStore.questions = [mixedQuestion];
    sandbox.setRedoHideAnswer(true);
    const mixedCard = figureCard();
    check('去重只掐重复的那张，另一张配图照常渲染',
        countOf(mixedCard, 'src="' + FIGURE_A + '"') === 1
        && countOf(mixedCard, 'src="' + FIGURE_B + '"') === 1);

    // ④ 过不了安全过滤的 URL 原样留着 —— 显示一串 markdown 顶多难看，
    //    静默抹掉会让老师以为这题本来就没配图。
    const unsafeQuestion = Object.assign({}, inlineOnlyQuestion, {
        display_content: '看这张图\n\n![插图](/static/uploads/mistakes/1/figures/vector.svg)'
            + '\n\n![插图](https://evil.example.com/shot.png)'
    });
    sandbox.RedoStore.questions = [unsafeQuestion];
    sandbox.setRedoHideAnswer(true);
    const unsafeCard = figureCard();
    check('非位图后缀（.svg）不生成 img，原样保留 markdown',
        !unsafeCard.includes('<img src="/static/uploads/mistakes/1/figures/vector.svg"')
        && unsafeCard.includes('![插图](/static/uploads/mistakes/1/figures/vector.svg)'));
    check('跨域 URL 不生成 img，原样保留 markdown',
        !unsafeCard.includes('<img src="https://evil.example.com/shot.png"')
        && unsafeCard.includes('![插图](https://evil.example.com/shot.png)'));

    // ⑤ 解析区同口径：答案里的图也要认（库里 20 道题命中）
    const answerFigureQuestion = Object.assign({}, inlineOnlyQuestion, {
        display_answer: '解析：见下图\n\n![插图](' + FIGURE_B + ')'
    });
    sandbox.RedoStore.questions = [answerFigureQuestion];
    sandbox.setRedoHideAnswer(true);
    check('隐藏答案时解析里的图不出现在卡片上', !figureCard().includes('src="' + FIGURE_B + '"'));
    sandbox.setRedoHideAnswer(false);
    const answerCard = figureCard();
    check('取消隐藏后解析里的图渲染成 <img>',
        answerCard.includes('<img src="' + FIGURE_B + '"') && !answerCard.includes('![插图]'));

    // ⑥ 正文图与 \begin{choices} 选项共存：图在选项块之前，两者都不能丢。
    //    这一项同时钉住「图插在选项之前」这条排版顺序 —— 与试卷 PDF 的
    //    stem_text → 图 → choices_part 一致（「如图，下列选项中…」的正确读法）。
    const figureWithChoices = Object.assign({}, inlineOnlyQuestion, {
        display_content: '如图，正确的是\n\n![插图](' + FIGURE_A + ')'
            + '\n\\begin{choices}\n\\item 甲\n\\item 乙\n\\end{choices}'
    });
    sandbox.RedoStore.questions = [figureWithChoices];
    sandbox.setRedoHideAnswer(true);
    const figureChoiceCard = figureCard();
    check('正文图与选项块共存：图和 A./B. 选项都在，且图在选项之前', (() => {
        const img = figureChoiceCard.indexOf('<img src="' + FIGURE_A + '"');
        const optA = figureChoiceCard.indexOf('A.</b> 甲');
        return img >= 0 && optA > img && !figureChoiceCard.includes('\\item');
    })(), figureChoiceCard.slice(0, 320));

    // ⑦ 没有图时不能凭空多出一个空的配图容器
    sandbox.RedoStore.questions = [Object.assign({}, QUESTION_3)];
    sandbox.setRedoHideAnswer(true);
    check('无图题目不渲染空的配图容器', !figureCard().includes(FIG_BLOCK_CLASS));

    // ⑧ 图长在选项里（`\item ![](a.png)`）—— 与 PDF 同口径：图从选项里抽出来、
    //    与正文图合并到配图块里（`paper_helper.py` 对整段 content 做 `re.sub` 摘图，
    //    选项里的图同样被提到题末）。所以这里钉两件事：图**在**、且选项里不留
    //    控制字符占位符残骸。
    sandbox.RedoStore.questions = [Object.assign({}, inlineOnlyQuestion, {
        display_content: '如图，正确的是\\begin{choices}\n\\item 甲\n\\item ![选项图](' + FIGURE_B + ')\n\\end{choices}'
    })];
    sandbox.setRedoHideAnswer(true);
    const optionFigureCard = figureCard();
    check('选项里带的图也渲染成 <img>',
        optionFigureCard.includes('<img src="' + FIGURE_B + '"'), optionFigureCard.slice(0, 320));
    check('选项里的图被抽到配图块、不留在选项里（与 PDF 摘图同口径）',
        !stemOf(optionFigureCard).includes('<img'), stemOf(optionFigureCard).slice(0, 200));
    check('选项里不留占位符残骸', !/\u0000MRIMG\d+\u0000/.test(optionFigureCard), optionFigureCard.slice(0, 320));

    const passed = total - failures;
    console.log('全部通过：' + passed + ' 项' + (failures ? '（失败 ' + failures + ' 项）' : ''));
    process.exit(failures ? 1 : 0);
}

main().catch((err) => {
    console.error('夹具自身异常: ' + (err && err.stack ? err.stack : err));
    process.exit(1);
});
