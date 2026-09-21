/**
 * 错题工作台 · 重做复习面板
 *
 * 它负责闭环的「后半程」：从重做池出卷 → 对照纸质卷逐题录入对错 → 掌握度回写。
 * 录入界面必须照着**导出时实际印在纸上**的形态判读（第 2 遍选项打乱过、第 3 遍
 * 干脆没有选项），所以卡片渲染的是后端给的 ``display_content``，不是库里的原题面。
 *
 * 视觉上一律复用 app.css 的 glass-* 与既有 Tailwind 配色，不引入新语言；
 * 挂载位置沿用「错题工作台内的一级子视图」写法（与 mistakeListView / ReviewView 同级）。
 */
(function () {
    'use strict';

    const MODE_LABEL = { plain: '原序', shuffled: '选项打乱', no_option: '无选项' };
    const MODE_HINT = {
        plain: '第 1 遍重做：选项顺序与原卷一致。',
        shuffled: '第 2 遍重做：选项已打乱，答案键同步重映射 —— 防「记住选项位置」。',
        no_option: '第 3 遍及以后：不提供选项，学生凭记忆作答。'
    };
    const MODE_CLASS = {
        plain: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
        shuffled: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
        no_option: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300'
    };
    const MASTERY_LABEL = { pending: '未掌握', redone: '重做对过', mastered: '已掌握' };
    const MASTERY_CLASS = {
        pending: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
        redone: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
        mastered: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
    };
    const STATUS_LABEL = { pending: '未录入', partial: '录入中', done: '已录入' };
    const STATUS_CLASS = {
        pending: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
        partial: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
        done: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
    };

    const state = {
        loaded: false,
        overview: null,
        tasks: [],
        activePaperId: null,
        paper: null,
        questions: [],
        index: 0,
        hideAnswer: true,
        busy: false,
        confirmDeleteId: null
    };

    function el(id) {
        return document.getElementById(id);
    }

    function esc(value) {
        if (window.MathBankSafe && typeof window.MathBankSafe.escapeText === 'function') {
            return window.MathBankSafe.escapeText(value);
        }
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    }

    function toast(message, type) {
        if (typeof window.showToast === 'function') window.showToast(message, type || 'info');
    }

    function todayIso() {
        const now = new Date();
        const pad = function (v) { return String(v).padStart(2, '0'); };
        return now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
    }

    async function getJson(url) {
        const res = await fetch(url);
        const body = await res.json().catch(function () { return {}; });
        return { ok: res.ok, status: res.status, data: body };
    }

    async function postJson(url, payload) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {})
        });
        const body = await res.json().catch(function () { return {}; });
        return { ok: res.ok, status: res.status, data: body };
    }

    // ---------------------------------------------------------------- 题面渲染

    /**
     * 正文内联图与「配图数组」两条路必须落在同一个尺寸上 —— 同一张图从哪条路
     * 出来长得不一样，老师会以为它们是两张图。共用一个常量就是为了掐死这件事。
     */
    const REDO_FIGURE_CLASS = 'max-h-56 rounded-lg border border-slate-200 dark:border-slate-700';

    function safeImageUrl(value) {
        const guard = window.MathBankSafe && window.MathBankSafe.safeImageUrl;
        return guard ? guard(value) : '';
    }

    function escAttr(value) {
        const guard = window.MathBankSafe && window.MathBankSafe.escapeAttribute;
        return guard ? guard(value) : esc(value);
    }

    /**
     * 题面 / 解析的正文渲染一律走 `MathRender.renderQuestionBody`（方案 B，2026-09-21）。
     *
     * 为什么不再自己写一份：原先 redo.js 漏了 `![alt](url)` 这一支，于是同一道题
     * 「导出的 PDF 上有图、重做页印出一串 markdown」。漏的不是图，是「哪些语法算图」
     * 这个决定被抄了四份。现在四份合成一份，这里只决定版式（图多大、要不要去重点）。
     */
    function renderBody(content, opts) {
        return window.MathRender.renderQuestionBody(content, opts);
    }

    /**
     * 题干的 HTML（含配图块）。
     *
     * 图**不留在正文流里**（`stripFigures`），而是先在**选项块之前**插成一整块 ——
     * 这正是试卷 PDF 的排版顺序（`paper_helper.py`：stem_text → 图 → choices_part），
     * 也是「如图，下列选项中…」这类题的读法：先题干、再图、最后才是 A./B./C./D.。
     *
     * 为什么值得统一：录入对错是**对着印出来的重做卷**判的。原先屏幕上图留在正文
     * 出现的原位、纸上却被抽到题末，同一道题两个位置，看着就不像同一张卷。
     */
    function questionStemHtml(item) {
        const block = figureBlockHtml(item);
        return renderBody(item.display_content, {
            figureClass: REDO_FIGURE_CLASS,
            stripFigures: true,
            beforeChoices: function (bodyHtml) {
                return bodyHtml + block;
            }
        }).html;
    }

    /** 解析/答案区：就地保留换行与正文内联图，并高亮 ``【答案】`` 标记。
     *
     * 解析里的图**不**抽到末尾：PDF 那一路（`paper_helper.py:clean_content_for_latex`）
     * 对 `answer_markdown` 就是就地转 `\includegraphics` 居中的，两边本来就一致；
     * 而且「由图可知…」这类话指着图说，挪走反而看不懂。 */
    function answerHtml(content) {
        const html = renderBody(content, { figureClass: REDO_FIGURE_CLASS }).html;
        return html.replace(/【答案】/g, '<b class="text-brand-700 dark:text-brand-500">【答案】</b>');
    }

    /**
     * 配图块：正文里出现的图（按出现顺序，含选项里的图）+ ``image_paths`` 里
     * 剩下的图，合并去重后画成一排、居中。
     *
     * 两路合并时必须去重：库里有 68 道题正文与 image_paths 指向同一个文件，
     * 不去重就会印出两张一模一样的图。
     *
     * 「正文里出现的图」直接用 `MathRender.inlineFigureUrls`（全站唯一的「什么算
     * 一张内联图」判定）扫一遍原文，而不是等渲染时收集 —— 因为这一块要排在**选项
     * 之前**，渲染到选项时再收就晚了；选项里带的图在 PDF 里同样是被抽出来提到
     * 题末的（`paper_helper.py` 对整段 content 做 `re.sub` 摘图），这里保持同口径。
     *
     * 居中即 PDF 的实际口径：库里 1399 道题 ``figure_align`` 有 1398 条是 'right'，
     * 那是**旧列默认值的残留** —— `paper_helper.py` 与 `paper.js:getQuestionFigAlign`
     * 都把「不带 custom 标记的 right」归一成 center，所以印出来实际全是题末居中。
     */
    function figureBlockHtml(item) {
        const urls = [];
        const add = function (value) {
            const safeUrl = safeImageUrl(value);
            if (safeUrl && urls.indexOf(safeUrl) === -1) urls.push(safeUrl);
        };
        window.MathRender.inlineFigureUrls(item.display_content || '').forEach(add);
        (Array.isArray(item.image_paths) ? item.image_paths : []).forEach(add);
        if (!urls.length) return '';
        return '<div class="flex flex-wrap justify-center gap-2 pt-1.5">' + urls.map(function (url) {
            return '<img src="' + escAttr(url) + '" alt="题图" loading="lazy" decoding="async" class="' + REDO_FIGURE_CLASS + '">';
        }).join('') + '</div>';
    }

    function renderMathIn(container) {
        if (container && window.MathRender && typeof window.MathRender.render === 'function') {
            window.MathRender.render(container, 'display');
        }
    }

    // ---------------------------------------------------------------- 子视图切换

    function setSubtabActive(which) {
        const scan = el('mistakeSubtabScan');
        const redo = el('mistakeSubtabRedo');
        if (scan) {
            scan.classList.toggle('active', which === 'scan');
            scan.setAttribute('aria-selected', which === 'scan' ? 'true' : 'false');
        }
        if (redo) {
            redo.classList.toggle('active', which === 'redo');
            redo.setAttribute('aria-selected', which === 'redo' ? 'true' : 'false');
        }
    }

    // 错题工作台是四个并列的顶层视图（批次列表 / 重做复习 / 错题本审校 / 批次详情），
    // 互相之间全靠每个入口自己记得隐藏别人。只藏一个的后果是：剩下两个 flex-1 视图
    // 同屏平分高度 —— 2026-09-20 实测，从组卷台认领跳到重做复习时批次详情（切题页）
    // 还在，重做复习页只拿到半屏，固定骨架吃掉 145px，题干区被压成 0，
    // 表现出来就是「题卡一片空白、只剩做错 / 做对按钮」。所以进任一视图前统一收口。
    const MISTAKE_TOP_VIEWS = [
        'mistakeListView', 'mistakeRedoView', 'mistakeReviewView', 'mistakeDetailView'
    ];

    /** 把错题工作台收敛成「只有 keepId 这一个视图可见」。 */
    function focusMistakeView(keepId) {
        MISTAKE_TOP_VIEWS.forEach(function (id) {
            if (id === keepId) return;
            const node = el(id);
            if (node) node.classList.add('hidden');
        });
    }

    function switchMistakeSubtab(which) {
        const listView = el('mistakeListView');
        const redoView = el('mistakeRedoView');
        if (which === 'redo') {
            focusMistakeView('mistakeRedoView');
            if (redoView) redoView.classList.remove('hidden');
            setSubtabActive('redo');
            openMistakeRedo();
        } else {
            focusMistakeView('mistakeListView');
            if (listView) listView.classList.remove('hidden');
            setSubtabActive('scan');
        }
    }

    function openMistakeRedo() {
        // 每次进来都重拉：错题入库、同步历史错题都会改重做池，缓存只会显示过期数字。
        state.loaded = true;
        loadOverview();
        loadTasks();
    }

    /**
     * 从别的工作台切回错题台时的落点。
     *
     * 四个顶层视图必须**互斥**：``openMistakeBatch`` 开详情前就关了列表，说明这是原设计。
     * 而初版这里只隐掉重做面板、又无条件把批次列表打开 —— 停在批次详情页时切回来，
     * 列表与详情会各拿一半高度（2026-09-20 用户截图：切题页漏在下半屏）。
     *
     * 所以：重做录入页照旧收起（既有约定，别停在上一轮的录入页），其余**谁开着就落谁**
     * （审校 > 详情 > 列表），一个都没开才落回批次列表 —— 既不叠加，也不丢正在切的题。
     */
    function landOnMistakeWorkspace() {
        const redoView = el('mistakeRedoView');
        if (redoView) redoView.classList.add('hidden');
        setSubtabActive('scan');
        const panel = el('redoNewPanel');
        if (panel) panel.classList.add('hidden');
        const board = el('redoBoardPanel');
        if (board) board.classList.add('hidden');

        let keepId = 'mistakeListView';
        const sticky = ['mistakeReviewView', 'mistakeDetailView'];
        for (let k = 0; k < sticky.length; k++) {
            const node = el(sticky[k]);
            if (node && !node.classList.contains('hidden')) { keepId = sticky[k]; break; }
        }
        focusMistakeView(keepId);
        const keep = el(keepId);
        if (keep) keep.classList.remove('hidden');
    }

    // 「← 返回批次列表」+ selectWorkspace 包装层都走这里。**刻意不碰 detailView /
    // reviewView**：从题库切回错题台时保留用户上次停留的批次详情页是既有行为，
    // 这里只负责收起重做面板本身。（认领跳转那条路径由 switchMistakeSubtab 收口。）
    function closeRedoReview() {
        const listView = el('mistakeListView');
        const redoView = el('mistakeRedoView');
        if (redoView) redoView.classList.add('hidden');
        if (listView) listView.classList.remove('hidden');
        setSubtabActive('scan');
        const panel = el('redoNewPanel');
        if (panel) panel.classList.add('hidden');
        const board = el('redoBoardPanel');
        if (board) board.classList.add('hidden');
    }

    async function refreshRedoBadge() {
        const dot = el('mistakeRedoDot');
        if (!dot) return;
        const res = await getJson('/api/redo/overview');
        if (!res.ok || !res.data || !res.data.stats) { dot.classList.add('hidden'); return; }
        dot.classList.toggle('hidden', !(res.data.stats.due_now > 0));
    }

    // ---------------------------------------------------------------- 总览与看板

    function statCardHtml(label, value, tone, note) {
        const toneClass = tone === 'warn'
            ? 'text-brand-700 dark:text-brand-500'
            : (tone === 'good' ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-700 dark:text-slate-200');
        return '<div class="glass-card rounded-xl px-3 py-2">' +
            '<div class="text-[10px] text-slate-500 mb-0.5">' + esc(label) + '</div>' +
            '<div class="text-lg font-semibold ' + toneClass + '">' + esc(value) + '</div>' +
            (note ? '<div class="text-[10px] text-slate-500 mt-0.5">' + esc(note) + '</div>' : '') +
            '</div>';
    }

    function renderStatBar() {
        const box = el('redoStatBar');
        if (!box) return;
        const stats = (state.overview && state.overview.stats) || null;
        if (!stats) {
            box.innerHTML = '<div class="col-span-full text-[11px] text-slate-400">统计加载中…</div>';
            return;
        }
        const manualMastered = Number(stats.mastered_manual || 0);
        box.innerHTML = [
            statCardHtml('待重做', stats.pending, stats.pending > 0 ? 'warn' : ''),
            statCardHtml('已到期（可出卷）', stats.due_now, stats.due_now > 0 ? 'warn' : ''),
            // 「已掌握」里混着两种来源：系统跨日连对 2 次判出来的，和老师手动标的。
            // 分开显示，老师才能复核自己标过的那些 —— 标多了会把该练的题漏掉。
            statCardHtml('已掌握', stats.mastered, 'good',
                manualMastered > 0 ? '其中 ' + manualMastered + ' 道人工标注' : ''),
            statCardHtml('反复错（≥2 次）', stats.stuck, stats.stuck > 0 ? 'warn' : ''),
            statCardHtml('累计重做次数', stats.redo_total, '')
        ].join('');

        const summary = el('redoSubSummary');
        if (summary) {
            const backfill = Number(stats.backfill_pending || 0);
            summary.textContent = '重做池 ' + stats.pool_total + ' 道 · 今日 ' + (state.overview.today || todayIso()) +
                (backfill > 0 ? ' · 还有 ' + backfill + ' 道历史错题未进池' : '');
        }

        // 「同步历史错题」只在真有活干的时候出现：常驻按钮会让人以为是每轮必点的一步。
        const syncBtn = el('redoSyncBtn');
        if (syncBtn) {
            const pendingWork = Number(stats.backfill_pending || 0) + Number(stats.answer_backfill_pending || 0);
            syncBtn.classList.toggle('hidden', pendingWork <= 0);
            const text = el('redoSyncBtnText');
            if (text) {
                text.textContent = Number(stats.backfill_pending || 0) > 0
                    ? ('同步历史错题（' + stats.backfill_pending + '）')
                    : ('补全答案字母（' + stats.answer_backfill_pending + '）');
            }
        }
    }

    function renderBoard() {
        const box = el('redoBoardPanel');
        if (!box) return;
        const board = (state.overview && state.overview.knowledge) || [];
        if (!board.length) {
            box.innerHTML = '<div class="text-[11px] text-slate-500">重做池还是空的 —— 错题入库后这里会按知识点汇总。</div>';
            return;
        }
        const maxWrong = Math.max.apply(null, board.map(function (item) { return item.wrong_total || 0; })) || 1;
        box.innerHTML = '<div class="text-[11px] font-semibold text-slate-500 mb-2">按知识点看「错得最多」的板块（累计错次降序）</div>' +
            '<div class="space-y-1.5">' + board.map(function (item) {
                const width = Math.max(4, Math.round((item.wrong_total || 0) / maxWrong * 100));
                return '<div class="flex items-center gap-2 text-[11px]">' +
                    '<div class="w-[38%] truncate text-slate-600 dark:text-slate-300" title="' + esc(item.knowledge) + '">' + esc(item.knowledge) + '</div>' +
                    '<div class="flex-1 h-2 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">' +
                        '<div class="h-full bg-brand-500/70" style="width:' + width + '%"></div></div>' +
                    '<div class="w-[7.5rem] text-right text-slate-500 shrink-0">错 ' + item.wrong_total + ' 次 / ' + item.pending + ' 道未掌握</div>' +
                    '</div>';
            }).join('') + '</div>';
    }

    async function loadOverview() {
        const res = await getJson('/api/redo/overview');
        if (!res.ok) {
            renderStatBar();
            toast((res.data && res.data.message) || '读取重做池失败', 'error');
            return;
        }
        state.overview = res.data;
        renderStatBar();
        renderBoard();
        const dot = el('mistakeRedoDot');
        if (dot) dot.classList.toggle('hidden', !(res.data.stats && res.data.stats.due_now > 0));
    }

    // ---------------------------------------------------------------- 任务列表

    function taskCardHtml(task) {
        const statusClass = STATUS_CLASS[task.status] || STATUS_CLASS.pending;
        const total = task.question_count || 0;
        const graded = task.graded_count || 0;
        const percent = total ? Math.round(graded / total * 100) : 0;
        const active = state.activePaperId === task.id;
        const confirmPending = state.confirmDeleteId === task.id;
        const dueText = task.due_date ? ('建议 ' + task.due_date) : '无待重做日';
        return '<div class="glass-list-item rounded-xl px-3 py-2.5' + (active ? ' active' : '') + '" data-redo-task="' + task.id + '">' +
            '<div class="flex items-start justify-between gap-2">' +
                '<div class="min-w-0 cursor-pointer" onclick="openRedoPaper(' + task.id + ')">' +
                    '<div class="text-xs font-semibold truncate" title="' + esc(task.title) + '">' + esc(task.title) + '</div>' +
                    '<div class="text-[10px] text-slate-500 mt-0.5">' +
                        (task.adopted
                            ? '<span class="text-emerald-600 dark:text-emerald-400 font-semibold">组卷台认领</span> · '
                            : '') +
                        '第 ' + (task.redo_round || 1) + ' 轮 · ' + total + ' 题 · ' +
                        graded + '/' + total + ' 已录入</div>' +
                '</div>' +
                '<span class="text-[10px] px-2 py-0.5 rounded-full shrink-0 ' + statusClass + '">' + esc(STATUS_LABEL[task.status] || task.status) + '</span>' +
            '</div>' +
            '<div class="mt-2 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">' +
                '<div class="h-full ' + (percent >= 100 ? 'bg-emerald-500' : 'bg-brand-500/70') + '" style="width:' + percent + '%"></div></div>' +
            '<div class="mt-2 flex items-center justify-between gap-2">' +
                '<span class="text-[10px] ' + (task.due_date && task.due_date <= todayIso() ? 'text-brand-700 dark:text-brand-500 font-semibold' : 'text-slate-500') + '">' +
                    esc(dueText) + ' · 累计错 ' + (task.wrong_total || 0) + ' 次</span>' +
                (confirmPending
                    ? '<span class="flex items-center gap-1 shrink-0">' +
                        '<button type="button" onclick="deleteRedoPaper(' + task.id + ', true)" class="glass-btn glass-btn-danger px-2 py-0.5 text-[10px] font-semibold">确认' + (task.adopted ? '取消认领' : '删除') + '</button>' +
                        '<button type="button" onclick="cancelRedoDelete()" class="glass-btn px-2 py-0.5 text-[10px] font-semibold">取消</button></span>'
                    : '<button type="button" title="' + (task.adopted
                        ? '取消认领：这份卷回到组卷历史，录入记录一并撤销（不动题库里的题）'
                        : '删除这份重做卷与它的录入记录（不动题库里的题）') + '" onclick="deleteRedoPaper(' + task.id + ', false)" class="glass-btn w-6 h-6 flex items-center justify-center rounded-lg shrink-0"><i class="fa-solid ' + (task.adopted ? 'fa-rotate-left' : 'fa-trash-can') + ' text-[10px]"></i></button>') +
            '</div></div>';
    }

    function renderTasks() {
        const box = el('redoTaskList');
        if (!box) return;
        const count = el('redoTaskCount');
        if (count) count.textContent = state.tasks.length + ' 份';
        if (!state.tasks.length) {
            box.innerHTML = '<div class="text-[11px] text-slate-400 leading-relaxed px-1 py-2">还没有重做卷。点右上角「重点复习卷」按到期日一键凑一份，或「生成重做卷」自选范围。</div>';
            return;
        }
        box.innerHTML = state.tasks.map(taskCardHtml).join('');
    }

    async function loadTasks() {
        const res = await getJson('/api/redo/tasks');
        if (!res.ok) {
            toast((res.data && res.data.message) || '读取重做卷失败', 'error');
            return;
        }
        state.tasks = res.data.tasks || [];
        renderTasks();
    }

    // ---------------------------------------------------------------- 录入区

    function currentQuestion() {
        return state.questions[state.index] || null;
    }

    function questionCardHtml(item, index, total) {
        const mode = item.option_mode || 'plain';
        const redo = item.redo || {};
        const recorded = item.recorded || null;
        // 人工标注掌握度：与「系统跨日连对 2 次」判出来的已掌握要能一眼分开 ——
        // 老师得知道自己标过哪几道，否则会把该练的题漏掉。
        const overrideActive = String(redo.mastery_override || '') === 'mastered';
        const overrideBadge = overrideActive
            ? '<span class="text-[10px] px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300">人工标注</span>'
            : '';
        // 认领卷里可能混着本来不是错题的题（组卷时一起加的）。它们**照样可以录对错** ——
        // 做错就自然进了重做池，拦住不让录反而更怪。只加一个中性提示说清来历。
        const notPooledBadge = Number(redo.wrong_count || 0) === 0
            ? '<span class="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400" title="这道题原本不在错题池里（组卷时一起加的）。学生做错后会自动进池。">非错题 · 做错后进池</span>'
            : '';
        const resultBadge = recorded
            ? (recorded.result === 'correct'
                ? '<span class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">已录：做对</span>'
                : '<span class="text-[10px] px-2 py-0.5 rounded-full bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300">已录：做错</span>')
            : '<span class="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">未录入</span>';
        const masteryClass = MASTERY_CLASS[redo.mastery_status] || MASTERY_CLASS.pending;
        // 题干的气泡里含题干文字 + 配图块（图在选项之前，见 questionStemHtml）。
        const stemHtml = questionStemHtml(item);
        return '<div class="glass-card rounded-2xl p-4 space-y-3">' +
            '<div class="flex items-center justify-between gap-2 flex-wrap">' +
                '<div class="flex items-center gap-1.5 flex-wrap">' +
                    '<span class="text-[11px] font-semibold text-slate-500">第 ' + (index + 1) + '/' + total + ' 题</span>' +
                    '<span class="text-[10px] px-2 py-0.5 rounded-full ' + (MODE_CLASS[mode] || MODE_CLASS.plain) + '">' + esc(MODE_LABEL[mode] || mode) + '</span>' +
                    '<span class="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300">第 ' + (item.attempt_no || 1) + ' 遍重做</span>' +
                    resultBadge +
                    notPooledBadge +
                '</div>' +
                '<div class="flex items-center gap-2 text-[10px]">' +
                    '<span class="text-rose-600 dark:text-rose-400 font-semibold">累计错 ' + (redo.wrong_count || 0) + ' 次</span>' +
                    '<span class="px-2 py-0.5 rounded-full ' + masteryClass + '">' + esc(MASTERY_LABEL[redo.mastery_status] || redo.mastery_status || '未掌握') + '</span>' +
                    overrideBadge +
                    (redo.next_redo_due ? '<span class="text-slate-500">建议 ' + esc(redo.next_redo_due) + '</span>' : '') +
                '</div>' +
            '</div>' +
            '<div class="text-[13px] leading-relaxed text-slate-800 dark:text-slate-100">' + stemHtml + '</div>' +
            '<div class="flex items-center justify-between gap-2 flex-wrap">' +
                '<div class="text-[10px] text-slate-400">' + esc(MODE_HINT[mode] || '') +
                    ' · 分值 ' + (item.score != null ? item.score : 5) +
                    (item.question_type === 'multi_choice' ? ' · 多选按全对计' : '') + '</div>' +
                '<button type="button" onclick="markRedoMastery(' + item.id + ', ' + (overrideActive ? 'false' : 'true') + ')" class="glass-btn px-2 py-0.5 text-[10px] font-semibold flex items-center space-x-1 shrink-0" title="' + (overrideActive ? '取消人工标注，回到按重做历史自动判定' : '老师手动标为已掌握：不再排进重做卷（之后若又做错，会自动回到未掌握）') + '">' +
                    '<i class="fa-solid ' + (overrideActive ? 'fa-rotate-left' : 'fa-circle-check') + ' text-[9px]"></i>' +
                    '<span>' + (overrideActive ? '取消掌握标注' : '标为已掌握') + '</span></button>' +
            '</div>' +
            (state.hideAnswer
                ? '<div class="rounded-xl border border-dashed border-slate-300 dark:border-slate-700 px-3 py-2 text-[11px] text-slate-400">答案已隐藏 —— 先对照纸质卷判对错，需要时取消勾选「隐藏答案」。</div>'
                : '<div class="rounded-xl bg-slate-50 dark:bg-slate-900/60 px-3 py-2 text-[12px] leading-relaxed text-slate-700 dark:text-slate-200">' +
                    (String(item.display_answer || '').trim() ? answerHtml(item.display_answer) : '<span class="text-slate-400">这道题没有录入解析。</span>') +
                  '</div>') +
            '</div>';
    }

    function renderGradeProgress() {
        const box = el('redoGradeProgress');
        if (!box) return;
        if (!state.paper) { box.innerHTML = ''; return; }
        const total = state.questions.length;
        const graded = state.questions.filter(function (q) { return !!q.recorded; }).length;
        const correct = state.questions.filter(function (q) { return q.recorded && q.recorded.result === 'correct'; }).length;
        box.innerHTML = '<div class="flex items-center justify-between gap-3 flex-wrap">' +
            '<span class="text-[11px] text-slate-500">录入进度 <b class="text-slate-700 dark:text-slate-200">' + graded + '/' + total + '</b>' +
                '（做对 ' + correct + ' · 做错 ' + (graded - correct) + '）</span>' +
            '<span class="text-[11px] text-slate-500">' +
                (state.paper.due_date ? '建议重做日 ' + esc(state.paper.due_date) : '本卷题目均已掌握') + '</span>' +
            '</div>';
    }

    function renderGradeCard() {
        const pane = el('redoGradePane');
        const empty = el('redoGradeEmpty');
        const card = el('redoGradeCard');
        if (!pane || !card) return;
        if (!state.paper || !state.questions.length) {
            pane.classList.add('hidden');
            if (empty) empty.classList.remove('hidden');
            return;
        }
        if (empty) empty.classList.add('hidden');
        pane.classList.remove('hidden');
        const title = el('redoGradeTitle');
        if (title) title.textContent = state.paper.title || '重做卷';
        const meta = el('redoGradeMeta');
        if (meta) {
            meta.textContent = '第 ' + (state.paper.redo_round || 1) + ' 轮 · ' + state.questions.length + ' 题 · ' +
                (STATUS_LABEL[state.paper.status] || state.paper.status);
        }
        card.innerHTML = questionCardHtml(currentQuestion(), state.index, state.questions.length);
        renderMathIn(card);
        renderGradeProgress();
    }

    async function openRedoPaper(paperId) {
        state.activePaperId = paperId;
        state.confirmDeleteId = null;
        renderTasks();
        const res = await getJson('/api/redo/papers/' + paperId);
        if (!res.ok) {
            toast((res.data && res.data.message) || '打开重做卷失败', 'error');
            return;
        }
        state.paper = res.data.paper;
        state.questions = res.data.questions || [];
        // 默认停在第一道还没录入的题上：周末收回来对着纸面录入时，要接着上次的地方走。
        const firstPending = state.questions.findIndex(function (q) { return !q.recorded; });
        state.index = firstPending >= 0 ? firstPending : 0;
        renderGradeCard();
    }

    function redoStep(delta) {
        if (!state.questions.length) return;
        const next = Math.min(state.questions.length - 1, Math.max(0, state.index + delta));
        if (next === state.index) return;
        state.index = next;
        renderGradeCard();
        const card = el('redoGradeCard');
        if (card) card.scrollTop = 0;
    }

    function setRedoHideAnswer(checked) {
        state.hideAnswer = !!checked;
        renderGradeCard();
    }

    async function gradeRedoCurrent(result) {
        if (state.busy) return;
        const item = currentQuestion();
        if (!item || !state.activePaperId) return;
        state.busy = true;
        try {
            const res = await postJson('/api/redo/papers/' + state.activePaperId + '/grade', {
                results: [{ question_id: item.id, result: result }]
            });
            if (!res.ok) {
                toast((res.data && res.data.message) || '保存录入失败', 'error');
                return;
            }
            if (res.data.rejected && res.data.rejected.length) {
                toast(res.data.rejected[0].reason || '这一题没能录入', 'error');
                return;
            }
            state.paper = res.data.paper || state.paper;
            state.questions = res.data.questions || state.questions;
            const redo = (state.questions[state.index] || {}).redo || {};
            if (redo.mastery_status === 'mastered') {
                toast('这一题已掌握，之后不再安排重做', 'success');
            }
            renderGradeCard();
            // 录完一题自动前进，省掉「点对错 + 点下一题」的重复动作。
            if (state.index < state.questions.length - 1) redoStep(1);
            // 必须重拉而不是 renderTasks()：左侧任务卡的「N/M 已录入」和进度条取的是
            // state.tasks 里的 graded_count，本地重绘只会画出录入前的旧数字。
            loadTasks();
            loadOverview();
        } finally {
            state.busy = false;
        }
    }

    async function markRedoMastery(questionId, mastered) {
        if (state.busy) return;
        // 按 id 定位而不是当前下标：卡片是按题重绘的，而这个动作是「对某一道题」的，
        // 请求往返期间用户切了题不能标错对象。
        const index = state.questions.findIndex(function (q) { return q.id === questionId; });
        if (index < 0) {
            toast('这道题不在当前重做卷里', 'error');
            return;
        }
        state.busy = true;
        try {
            const res = await postJson('/api/redo/questions/' + questionId + '/mastery', {
                mastered: !!mastered
            });
            if (!res.ok) {
                toast((res.data && res.data.message) || '标注失败', 'error');
                return;
            }
            // 服务端会给重放后的真实状态 —— 直接采信它，别在本地猜：
            // 标注可能被更晚的录入推翻（事件归并），本地算不出来。
            if (res.data.redo) {
                state.questions[index] = Object.assign({}, state.questions[index], {
                    redo: res.data.redo
                });
            }
            if (state.index === index) renderGradeCard();
            toast(
                res.data.mastered ? '已标为掌握，不再排进重做卷' : '已取消标注，回到按重做历史判定',
                'success'
            );
            loadTasks();
            loadOverview();
        } finally {
            state.busy = false;
        }
    }

    // ---------------------------------------------------------------- 出卷

    function fillRedoSubjectOptions() {
        const select = el('redoSubjectSelect');
        if (!select || select.dataset.filled === '1') return;
        const meta = window.systemMetadata || {};
        const options = [{ value: '', label: '全部学科' }];
        const subjects = meta.subjects || meta.subject_options;
        if (Array.isArray(subjects)) {
            subjects.forEach(function (item) {
                const value = typeof item === 'string' ? item : (item.value || item.code || '');
                const label = typeof item === 'string' ? item : (item.label || item.name || value);
                if (value) options.push({ value: value, label: label });
            });
        }
        if (options.length === 1) {
            [['math', '数学'], ['physics', '物理'], ['chemistry', '化学']].forEach(function (pair) {
                options.push({ value: pair[0], label: pair[1] });
            });
        }
        select.innerHTML = options.map(function (item) {
            return '<option value="' + esc(item.value) + '">' + esc(item.label) + '</option>';
        }).join('');
        select.dataset.filled = '1';
    }

    function toggleRedoNewPanel() {
        const panel = el('redoNewPanel');
        if (!panel) return;
        const willShow = panel.classList.contains('hidden');
        panel.classList.toggle('hidden', !willShow);
        if (willShow) {
            fillRedoSubjectOptions();
            const hint = el('redoNewHint');
            if (hint && state.overview && state.overview.stats) {
                const stats = state.overview.stats;
                hint.textContent = '当前可出题：' + (stats.due_now || 0) + ' 道已到期 · ' +
                    (stats.pending || 0) + ' 道未掌握。按累计错次从高到低取题；建议重做日一律落在周六。';
            }
        }
    }

    async function createRedoPaper(payload, autoOpen) {
        const res = await postJson('/api/redo/papers', payload);
        if (!res.ok) {
            toast((res.data && res.data.message) || '生成重做卷失败', 'error');
            return null;
        }
        toast('已生成重做卷：' + res.data.question_count + ' 题（第 ' + res.data.redo_round + ' 轮）', 'success');
        await loadTasks();
        await loadOverview();
        const panel = el('redoNewPanel');
        if (panel) panel.classList.add('hidden');
        if (autoOpen !== false) await openRedoPaper(res.data.paper_id);
        return res.data.paper_id;
    }

    async function submitRedoNewPaper() {
        const scopeEl = el('redoScopeSelect');
        const subjectEl = el('redoSubjectSelect');
        const limitEl = el('redoLimitInput');
        const titleEl = el('redoTitleInput');
        const limit = parseInt((limitEl && limitEl.value) || '12', 10);
        return createRedoPaper({
            scope: scopeEl ? scopeEl.value : 'due',
            subject: subjectEl ? subjectEl.value : '',
            limit: isNaN(limit) ? 12 : limit,
            title: titleEl ? titleEl.value : ''
        }, true);
    }

    async function createQuickRedoPaper() {
        const stats = (state.overview && state.overview.stats) || {};
        const limit = Math.max(1, Math.min(30, Number(stats.due_now || 0) || 12));
        return createRedoPaper({ scope: 'due', limit: limit }, true);
    }

    async function syncRedoPool() {
        const btn = el('redoSyncBtn');
        if (btn) btn.disabled = true;
        try {
            const res = await postJson('/api/redo/pool/sync', {});
            if (!res.ok) {
                toast((res.data && res.data.message) || '同步失败', 'error');
                return;
            }
            const parts = [];
            if (res.data.seeded) parts.push('补入 ' + res.data.seeded + ' 道历史错题');
            if (res.data.answer_filled) parts.push('补全 ' + res.data.answer_filled + ' 道题的答案字母');
            toast(parts.length ? ('同步完成：' + parts.join('、')) : '没有需要补的数据', parts.length ? 'success' : 'info');
            await loadOverview();
            await loadTasks();
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function toggleRedoBoard() {
        const box = el('redoBoardPanel');
        if (!box) return;
        const willShow = box.classList.contains('hidden');
        box.classList.toggle('hidden', !willShow);
        if (willShow) renderBoard();
    }

    // ---------------------------------------------------------------- 导出与删除

    function triggerDownload(url, filename) {
        const link = document.createElement('a');
        link.href = url;
        if (filename) link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    async function exportRedoPaper(withAnswers) {
        if (!state.activePaperId) {
            toast('先打开一份重做卷', 'info');
            return;
        }
        // 先开空标签页：编译要几秒，等 await 之后再 window.open 会被浏览器拦掉。
        const tab = window.open('', '_blank');
        if (tab && tab.document) {
            tab.document.write('<title>正在编译…</title><p style="font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\',sans-serif;padding:24px;color:#64748b">正在编译 PDF，请稍候…</p>');
        }
        toast('正在编译 PDF…', 'info');
        try {
            const res = await fetch('/api/redo/papers/' + state.activePaperId + '/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ format: 'pdf', include_answers: !!withAnswers })
            });
            if (res.ok && (res.headers.get('content-type') || '').indexOf('application/pdf') !== -1) {
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                let filename = 'redopaper.pdf';
                const encoded = res.headers.get('X-Redo-Filename');
                if (encoded) {
                    try { filename = decodeURIComponent(encoded); } catch (error) { /* 用默认名 */ }
                }
                if (tab && !tab.closed) {
                    tab.location.href = url;
                } else {
                    triggerDownload(url, filename);
                }
                toast('PDF 编译成功' + (withAnswers ? '（含答案解析，别给学生）' : ''), 'success');
            } else {
                const body = await res.json().catch(function () { return {}; });
                const message = body.message || 'PDF 编译失败';
                if (tab && !tab.closed) {
                    tab.document.write('<title>编译失败</title><p style="font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\',sans-serif;padding:24px;color:#b91c1c">' + esc(message) + '</p>');
                }
                toast(message, 'error');
            }
        } catch (error) {
            if (tab && !tab.closed) tab.close();
            toast('导出异常：' + (error && error.message ? error.message : error), 'error');
        }
    }

    function deleteRedoPaper(paperId, confirmed) {
        if (!confirmed) {
            state.confirmDeleteId = paperId;
            renderTasks();
            // 5 秒内没点确认就自动收回，避免卡片上长期挂着一个「确认删除」。
            setTimeout(function () {
                if (state.confirmDeleteId === paperId) {
                    state.confirmDeleteId = null;
                    renderTasks();
                }
            }, 5000);
            return;
        }
        state.confirmDeleteId = null;
        fetch('/api/redo/papers/' + paperId, { method: 'DELETE' })
            .then(function (res) { return res.json().catch(function () { return {}; }); })
            .then(function (data) {
                if (data && data.status === 'success') {
                    if (state.activePaperId === paperId) {
                        state.activePaperId = null;
                        state.paper = null;
                        state.questions = [];
                        state.index = 0;
                        renderGradeCard();
                    }
                    toast('重做卷已删除', 'success');
                    loadTasks();
                    loadOverview();
                } else {
                    toast((data && data.message) || '删除失败', 'error');
                }
            })
            .catch(function (error) {
                toast('删除失败：' + (error && error.message ? error.message : error), 'error');
            });
    }

    function cancelRedoDelete() {
        state.confirmDeleteId = null;
        renderTasks();
    }

    /** 组卷台历史卷卡片上的「录入结果」：切到错题台并把这张卷打开。 */
    async function openRedoPaperFromLibrary(paperId) {
        const id = parseInt(paperId, 10) || 0;
        if (!id) return;
        if (typeof window.selectWorkspace === 'function') {
            window.selectWorkspace('mistake', '错题工作台');
        }
        switchMistakeSubtab('redo');
        await openRedoPaper(id);
    }

    // ---------------------------------------------------------------- 挂载

    window.switchMistakeSubtab = switchMistakeSubtab;
    window.openMistakeRedo = openMistakeRedo;
    window.closeRedoReview = closeRedoReview;
    window.toggleRedoNewPanel = toggleRedoNewPanel;
    window.submitRedoNewPaper = submitRedoNewPaper;
    window.createQuickRedoPaper = createQuickRedoPaper;
    window.syncRedoPool = syncRedoPool;
    window.toggleRedoBoard = toggleRedoBoard;
    window.openRedoPaper = openRedoPaper;
    window.redoStep = redoStep;
    window.gradeRedoCurrent = gradeRedoCurrent;
    window.markRedoMastery = markRedoMastery;
    window.setRedoHideAnswer = setRedoHideAnswer;
    window.exportRedoPaper = exportRedoPaper;
    window.deleteRedoPaper = deleteRedoPaper;
    window.cancelRedoDelete = cancelRedoDelete;
    window.refreshRedoBadge = refreshRedoBadge;
    window.openRedoPaperFromLibrary = openRedoPaperFromLibrary;
    window.RedoStore = state;

    // mistake.js 已经包装过一次 selectWorkspace（保存原始引用 + 包装）。这里再包一层，
    // 顺序上本模块后加载、包裹在最外层，所以要在调用完前一层之后把错题台内部这一层的
    // 显隐再修正一遍 —— 否则从题库切回错题台会停在上一轮的录入页，或者列表与切题详情
    // 同屏平分高度（见 landOnMistakeWorkspace 的注释）。
    const previousSelectWorkspace = window.selectWorkspace;
    window.selectWorkspace = function (workspaceId, workspaceName) {
        if (typeof previousSelectWorkspace === 'function') {
            previousSelectWorkspace(workspaceId, workspaceName);
        }
        if (workspaceId === 'mistake') {
            landOnMistakeWorkspace();
            refreshRedoBadge();
        }
    };

    document.addEventListener('DOMContentLoaded', function () {
        fillRedoSubjectOptions();
        // 角标是「今天该重做几道」的提醒，进错题台之前就要能看到。
        refreshRedoBadge();
    });
})();
