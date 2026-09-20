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
    'mistakeListView', 'mistakeRedoView', 'mistakeSubtabScan', 'mistakeSubtabRedo',
    'mistakeRedoDot', 'redoSubSummary', 'redoStatBar', 'redoSyncBtn', 'redoSyncBtnText',
    'redoBoardBtn', 'redoBoardPanel', 'redoQuickBtn', 'redoNewBtn', 'redoNewPanel',
    'redoScopeSelect', 'redoSubjectSelect', 'redoLimitInput', 'redoTitleInput',
    'redoNewHint', 'redoTaskList', 'redoTaskCount', 'redoGradeEmpty', 'redoGradePane',
    'redoGradeTitle', 'redoGradeMeta', 'redoHideAnswerToggle', 'redoGradeProgress',
    'redoGradeCard',
    'redoAdoptPrompt', 'redoAdoptText', 'redoAdoptConfirmBtn', 'redoAdoptDismissBtn'
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
elements.redoNewPanel.classList.add('hidden');
elements.redoBoardPanel.classList.add('hidden');
elements.redoGradePane.classList.add('hidden');
// 确认卡在 index.html 里也是带 hidden 的静态结构（显示时补 flex）—— 夹具必须同源。
elements.redoAdoptPrompt.classList.add('hidden');
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
    // 认领四件套。paper_id=13 用来演「不值得认领」那一档（没有错题 / 已说过普通卷）。
    if (url === '/api/redo/eligibility') {
        const body = JSON.parse(options.body || '{}');
        if (Number(body.paper_id) === 13) {
            return jsonResponse({
                status: 'success', total: 3, pooled_count: 0, pooled_ids: [],
                already_redo: false, declined: true, eligible: false
            });
        }
        return jsonResponse({
            status: 'success', total: 3, pooled_count: 2, pooled_ids: [101, 102],
            already_redo: false, declined: false, eligible: true
        });
    }
    if (url === '/api/redo/adopt') {
        const body = JSON.parse(options.body || '{}');
        return jsonResponse({
            status: 'success',
            paper_id: Number(body.paper_id) || 12,
            title: body.title || '高一上第3周错题练习',
            created: !body.paper_id,
            already: false,
            redo_round: 2,
            question_count: 3,
            pooled_count: 2,
            message: '已记作重做练习。做完后到错题工作台的「重做复习」里逐题录入对错。'
        });
    }
    if (url === '/api/redo/decline') {
        return jsonResponse({ status: 'success', paper_id: Number(JSON.parse(options.body || '{}').paper_id) || 0 });
    }
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
    MathRender: { render: (container) => { mathRenders.push(container && container.id); return true; } },
    MathBankSafe: {
        escapeText: (value) => String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#039;'),
        safeImageUrl: (value) => (value ? value : '')
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
    sandbox.switchMistakeSubtab('redo');
    await tick(); await tick();
    check('切到重做面板后批次列表隐藏', el('mistakeListView').classList.contains('hidden'));
    check('切到重做面板后重做视图显示', !el('mistakeRedoView').classList.contains('hidden'));
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

    // ==================== 13. 认领组卷台那张卷 ====================
    //
    // 断点：错题闭环的第 1 遍练习几乎必然发生在组卷台（入库即送组卷 → 排版 → 印出来），
    // 可那张卷不带 is_redo，它的做题结果没有任何入口写回 redo_attempts。确认卡是补上的
    // 那座桥，问在导出之后。要钉两件事：认领导向**这张卷**而不是「当前选中」；点
    // 「这是普通卷」要真去落库，否则每重印一次就要再答一遍。

    check('任务卡上认领卷有「组卷台认领」标记',
        el('redoTaskList').innerHTML.includes('组卷台认领'));

    // ① 已保存的卷：带 paper_id 认领
    requests.length = 0;
    const prompted = await sandbox.maybePromptRedoAdopt({ paperId: 12 });
    const eligReq = requests.filter((r) => r.url === '/api/redo/eligibility').pop();
    check('导出后先问一句资格（POST /api/redo/eligibility）',
        prompted === true && !!eligReq && eligReq.method === 'POST');
    check('已保存的卷按 paper_id 问', JSON.parse(eligReq.options.body).paper_id === 12);
    check('弹卡：去掉 hidden 并补上 flex（index.html 里初始没有 flex）',
        !el('redoAdoptPrompt').classList.contains('hidden')
        && el('redoAdoptPrompt').classList.contains('flex'));
    check('文案说清「含几道错题」与「不会重新打乱」',
        el('redoAdoptText').innerHTML.includes('2')
        && el('redoAdoptText').innerHTML.includes('不会重新打乱'));

    requests.length = 0;
    sandbox.confirmRedoAdopt();
    await tick(); await tick();
    const adoptReq = requests.filter((r) => r.url === '/api/redo/adopt').pop();
    check('点「记作重做练习」走 POST /api/redo/adopt',
        !!adoptReq && adoptReq.method === 'POST');
    check('认领导向这张卷（paper_id=12），不是「当前选中项」',
        JSON.parse(adoptReq.options.body).paper_id === 12);
    check('确认后卡片收起', el('redoAdoptPrompt').classList.contains('hidden'));
    check('认领后顺手把这卷打开，可以马上录对错',
        requests.some((r) => r.url === '/api/redo/papers/12'));

    // ② 未落库的卷面（主编辑器导出）：按 question_ids 查、把卷面带过去现场建卷
    requests.length = 0;
    await sandbox.maybePromptRedoAdopt({
        questionIds: [101, 102],
        draft: { title: '刚组好的卷', paper_type: 'exam', questions: [{ id: 101, score: 5 }] }
    });
    const draftElig = requests.filter((r) => r.url === '/api/redo/eligibility').pop();
    const draftEligBody = JSON.parse(draftElig.options.body);
    check('没有 paper_id 时按 question_ids 问资格',
        !draftEligBody.paper_id && Array.isArray(draftEligBody.question_ids)
        && draftEligBody.question_ids.length === 2);
    requests.length = 0;
    sandbox.confirmRedoAdopt();
    await tick(); await tick();
    const draftAdopt = requests.filter((r) => r.url === '/api/redo/adopt').pop();
    const draftAdoptBody = JSON.parse(draftAdopt.options.body);
    check('未落库的卷面把卷面带过去让后端现场建卷',
        !draftAdoptBody.paper_id && draftAdoptBody.title === '刚组好的卷'
        && Array.isArray(draftAdoptBody.questions));

    // ③ 未落库的卷面说「普通卷」只记在本次会话（没有 paper_id 可落库）
    await sandbox.maybePromptRedoAdopt({ questionIds: [101, 103], draft: { title: '另一份卷' } });
    requests.length = 0;
    sandbox.dismissRedoAdopt();
    await tick();
    check('没有 paper_id 时说「普通卷」只记在会话内（不发接口）',
        requests.filter((r) => r.url === '/api/redo/decline').length === 0);

    // ④ 同一份卷面不再问 —— 否则重印一次问一次
    requests.length = 0;
    const askedAgain = await sandbox.maybePromptRedoAdopt({
        questionIds: [101, 103], draft: { title: '另一份卷' }
    });
    check('同一份卷面说过「普通卷」之后完全不再发问',
        askedAgain === false && requests.length === 0);

    // ⑤ 已保存的卷说「普通卷」要落库（跨会话有效）
    await sandbox.maybePromptRedoAdopt({ paperId: 12 });
    requests.length = 0;
    sandbox.dismissRedoAdopt();
    await tick(); await tick();
    const declineReq = requests.filter((r) => r.url === '/api/redo/decline').pop();
    check('已保存的卷说「普通卷」要落库（重印不再追问）',
        !!declineReq && JSON.parse(declineReq.options.body).paper_id === 12);

    // ⑥ 不值得认领（没有错题 / 已认领 / 已说过普通卷）就不弹
    requests.length = 0;
    const notEligible = await sandbox.maybePromptRedoAdopt({ paperId: 13 });
    check('不满足条件就不弹卡',
        notEligible === false && el('redoAdoptPrompt').classList.contains('hidden'));

    // ⑦ 认领卷里的非错题：标出来，但照样能录（做错就进池）
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

    const passed = total - failures;
    console.log('全部通过：' + passed + ' 项' + (failures ? '（失败 ' + failures + ' 项）' : ''));
    process.exit(failures ? 1 : 0);
}

main().catch((err) => {
    console.error('夹具自身异常: ' + (err && err.stack ? err.stack : err));
    process.exit(1);
});
