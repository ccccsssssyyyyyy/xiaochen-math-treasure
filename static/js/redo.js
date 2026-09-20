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
        confirmDeleteId: null,
        // 导出后那张「要不要记作重做练习」的确认卡上下文（见 maybePromptRedoAdopt）。
        adoptContext: null
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
     * 只处理错题/重做卷会遇到的几种结构：choices 环境、\fillin、\paren，其余交给 KaTeX。
     * 与 mistake.js 的预览同口径 —— 两边对同一道题的呈现必须一致，否则老师会以为
     * 「导出的卷子和页面看到的不一样」。
     */
    function latexPreviewHtml(content) {
        let text = String(content || '').trim();
        const renderStack = [];
        text = text.replace(/\\begin\{choices\}([\s\S]*?)\\end\{choices\}/g, function (_m, inner) {
            const items = inner.split(/\\item\b/)
                .map(function (piece) { return piece.trim(); })
                .filter(function (piece) { return piece.length > 0; });
            renderStack.push(items);
            return '';
        }).trim();
        let html = esc(text)
            .replace(/\\fillin\b/g, '<span class="inline-block min-w-[3rem] border-b border-slate-400">&nbsp;</span>')
            .replace(/\\paren\b/g, '<span class="inline-block">&nbsp;（&nbsp;&nbsp;&nbsp;）</span>');
        html = html
            .replace(/\n{2,}/g, '<span class="block h-2"></span>')
            .replace(/\n/g, '<br>');
        const choices = renderStack[0] || [];
        if (choices.length) {
            const letters = 'ABCDEFGH';
            html += '<div class="mt-1.5 grid grid-cols-1 gap-0.5 text-[12px]">' +
                choices.map(function (item, index) {
                    return '<div><b class="text-slate-400">' + (letters[index] || (index + 1)) + '.</b> ' + esc(item) + '</div>';
                }).join('') + '</div>';
        }
        return html;
    }

    /** 解析/答案区：保留换行、交给 KaTeX 渲染公式，并高亮 ``【答案】`` 标记。 */
    function answerHtml(content) {
        let html = esc(String(content || '').trim())
            .replace(/\n/g, '<br>');
        html = html.replace(/【答案】/g, '<b class="text-brand-700 dark:text-brand-500">【答案】</b>');
        return html;
    }

    function figureStripHtml(item) {
        const paths = Array.isArray(item.image_paths) ? item.image_paths : [];
        if (!paths.length) return '';
        const safeUrl = (window.MathBankSafe && window.MathBankSafe.safeImageUrl)
            ? window.MathBankSafe.safeImageUrl : function () { return ''; };
        const usable = paths.map(safeUrl).filter(function (u) { return !!u; });
        if (!usable.length) return '';
        return '<div class="flex flex-wrap gap-2 pt-1.5">' + usable.map(function (url) {
            return '<img src="' + esc(url) + '" alt="题图" loading="lazy" decoding="async" class="max-h-56 rounded-lg border border-slate-200 dark:border-slate-700">';
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

    function switchMistakeSubtab(which) {
        const listView = el('mistakeListView');
        const redoView = el('mistakeRedoView');
        if (which === 'redo') {
            if (listView) listView.classList.add('hidden');
            if (redoView) redoView.classList.remove('hidden');
            setSubtabActive('redo');
            openMistakeRedo();
        } else {
            if (redoView) redoView.classList.add('hidden');
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
            '<div class="text-[13px] leading-relaxed text-slate-800 dark:text-slate-100">' + latexPreviewHtml(item.display_content) + '</div>' +
            figureStripHtml(item) +
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

    // ---------------------------------------------------------------- 认领为重做练习
    //
    // 错题闭环的第 1 遍练习几乎必然发生在**组卷台**那条通道（入库即送组卷 → 排版 →
    // 印出来），可那张卷不带 is_redo，它的做题结果本来没有任何入口写回
    // redo_attempts：老师只能去重做台重新生成一张，题序与手上纸卷对不上，轮次还会
    // 被少算一遍（redo_round 取的是 max(attempt_no)，而这遍没记账）。
    //
    // 这一问就是补上那座桥，问在**导出之后** —— 那正是「学生要做这份卷」的时刻。
    // 导出 PDF 是二进制响应，塞不进「这份卷含几道错题」，所以另发一问 eligibility。

    // 同一会话里已明确说过「这是普通卷」的卷面签名。落库的卷把标记写进
    // metadata.redo_declined（跨会话有效）；没落库的（主编辑器直接导出）只能用这个
    // 内存集合兜住 —— 打印前一遍又一遍问同一件事会很烦。
    var declinedDraftSignatures = {};

    function draftSignature(questionIds) {
        return (questionIds || []).slice()
            .map(function (v) { return parseInt(v, 10) || 0; })
            .sort(function (a, b) { return a - b; })
            .join(',');
    }

    function showRedoAdoptPrompt() {
        const box = el('redoAdoptPrompt');
        if (!box) return;
        box.classList.remove('hidden');
        box.classList.add('flex');
        box.setAttribute('aria-hidden', 'false');
    }

    function hideRedoAdoptPrompt() {
        const box = el('redoAdoptPrompt');
        if (!box) return;
        box.classList.add('hidden');
        box.classList.remove('flex');
        box.setAttribute('aria-hidden', 'true');
    }

    /**
     * 导出成功之后问一句：这份卷要不要记作重做练习？返回 true 表示弹了卡。
     *
     * options = { paperId?, questionIds?, draft? }
     *   - 历史卷快速导出 → 有 paperId（declined 能落库，之后永久不再问）
     *   - 主编辑器导出   → 没有 paperId（卷面还在试题篮里），带 draft 供认领时现场落库
     */
    async function maybePromptRedoAdopt(options) {
        const opts = options || {};
        const paperId = parseInt(opts.paperId, 10) || 0;
        const ids = (opts.questionIds || [])
            .map(function (v) { return parseInt(v, 10) || 0; })
            .filter(function (v) { return v > 0; });
        if (!paperId && !ids.length) return false;
        if (!paperId && declinedDraftSignatures[draftSignature(ids)]) return false;

        let res = null;
        try {
            res = await postJson(
                '/api/redo/eligibility',
                paperId ? { paper_id: paperId } : { question_ids: ids }
            );
        } catch (e) {
            return false;
        }
        if (!res || !res.ok || !res.data || !res.data.eligible) return false;

        state.adoptContext = {
            paperId: paperId,
            questionIds: ids,
            draft: opts.draft || null,
            pooledCount: res.data.pooled_count
        };
        const text = el('redoAdoptText');
        if (text) {
            text.innerHTML = '这份卷里有 <b>' + esc(String(res.data.pooled_count)) + '</b> 道错题。' +
                '记作重做练习后，做完就能在错题工作台里逐题录入对错，' +
                '掌握度和下次重做的日子才会开始累积。' +
                '<br><span class="text-slate-400">卷面顺序保持原样、不会重新打乱 —— 跟你手上那张纸卷一致。</span>';
        }
        showRedoAdoptPrompt();
        return true;
    }

    async function confirmRedoAdopt() {
        const ctx = state.adoptContext;
        hideRedoAdoptPrompt();
        if (!ctx) return;
        const btn = el('redoAdoptConfirmBtn');
        if (btn) btn.disabled = true;
        try {
            const payload = ctx.paperId
                ? { paper_id: ctx.paperId }
                : Object.assign({}, ctx.draft || {});
            const res = await postJson('/api/redo/adopt', payload);
            if (!res.ok) {
                toast((res.data && res.data.message) || '记作重做练习失败', 'error');
                return;
            }
            state.adoptContext = null;
            toast(res.data.message || '已记作重做练习', 'success');
            await refreshRedoBadge();
            // 顺手把这卷打开，老师可以马上对着纸卷录对错。
            if (res.data.paper_id) {
                if (typeof window.selectWorkspace === 'function') {
                    window.selectWorkspace('mistake', '错题工作台');
                }
                switchMistakeSubtab('redo');
                await openRedoPaper(res.data.paper_id);
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function dismissRedoAdopt() {
        const ctx = state.adoptContext;
        hideRedoAdoptPrompt();
        if (!ctx) return;
        state.adoptContext = null;
        if (!ctx.paperId) {
            declinedDraftSignatures[draftSignature(ctx.questionIds)] = true;
            toast('好的，这份卷保持普通卷。', 'info');
            return;
        }
        // 落库的卷把「这是普通卷」记在 metadata 上，之后重印不再问。
        await postJson('/api/redo/decline', { paper_id: ctx.paperId });
        toast('好的，这份卷保持普通卷，之后不再追问。', 'info');
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
    window.maybePromptRedoAdopt = maybePromptRedoAdopt;
    window.confirmRedoAdopt = confirmRedoAdopt;
    window.dismissRedoAdopt = dismissRedoAdopt;
    window.openRedoPaperFromLibrary = openRedoPaperFromLibrary;
    window.RedoStore = state;

    // mistake.js 已经包装过一次 selectWorkspace（保存原始引用 + 包装）。这里再包一层，
    // 顺序上本模块后加载、包裹在最外层，所以要在调用完前一层之后把「重做面板」这一层
    // 的显隐再修正一遍 —— 否则从题库切回错题台会停在上一轮的录入页。
    const previousSelectWorkspace = window.selectWorkspace;
    window.selectWorkspace = function (workspaceId, workspaceName) {
        if (typeof previousSelectWorkspace === 'function') {
            previousSelectWorkspace(workspaceId, workspaceName);
        }
        if (workspaceId === 'mistake') {
            closeRedoReview();
            refreshRedoBadge();
        }
    };

    document.addEventListener('DOMContentLoaded', function () {
        fillRedoSubjectOptions();
        // 角标是「今天该重做几道」的提醒，进错题台之前就要能看到。
        refreshRedoBadge();
    });
})();
