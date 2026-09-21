/**
 * 错题工作台 —— 扫描 → 切题 → 点选 → 识别 → 错题本 / 入库
 *
 * 设计依据：docs/错题扫描与错题本-实施计划-2026-09-13.md
 *
 * 三条贯穿全篇的原则：
 * 1. **点选默认「未批」**：宁可让用户多按一次，也不要让「漏点」被当成「做对了」。
 * 2. **识别只跑错题**：进度条的分母是选中题数，不是总题块数；按钮文案必须说清这点。
 * 3. **AI 生成的解析不进 PDF**：必须人工核对过（answer_reviewed）才允许出现在错题本里，
 *    错误解析比没有解析更糟。
 *
 * 输入图已由上游工具去掉手写作答与红笔批改，本模块不做任何图像处理 —— 页面上那句
 * 「判断依据请对照原件 / 纸质卷」是这条约定的 UX 补偿，不要删。
 */
(function () {
    'use strict';

    const SUBJECT_LABELS = { math: '数学', physics: '物理', chemistry: '化学', other: '其他' };
    const STATUS_LABELS = {
        pending: '待切题', cutting: '切题中', cut_failed: '切题失败',
        reviewing: '待点选', done: '已出 PDF'
    };
    const GRAD_META = {
        correct: { label: '对', icon: 'fa-check', cls: 'bg-emerald-500 text-white border-emerald-500' },
        incorrect: { label: '错', icon: 'fa-xmark', cls: 'bg-red-500 text-white border-red-500' },
        unknown: { label: '未批', icon: 'fa-question', cls: 'bg-white text-slate-400 border-slate-300' }
    };
    // 页图上题块的染色：只染「错」与「对」，未批保持透明，避免满屏色块看不清原卷
    const BLOCK_TINT = {
        incorrect: 'rgba(239,68,68,0.22)',
        correct: 'rgba(16,185,129,0.16)',
        unknown: 'rgba(148,163,184,0.06)'
    };
    const BLOCK_BORDER = {
        incorrect: '#ef4444', correct: '#10b981', unknown: '#94a3b8'
    };
    // 人工没给分栏线、自动也判不出时画在正中的占位线（与后端 DEFAULT_COLUMN_BOUNDARY 一致）。
    // 这是「预估线」：真值由后端按新栏目重切时自动补，重切后会用真实的分栏线刷新。
    const DEFAULT_COLUMN_BOUNDARY = 0.5;
    const LAYOUT_SWITCH = [
        { key: 'auto', label: '自动', title: '用自动判栏的结果' },
        { key: 'single', label: '单栏', title: '整页一栏' },
        { key: 'double', label: '双栏', title: '左右分栏，可拖动分栏线' }
    ];
    //: 拖完分栏线后这段时间内的 click 一律吞掉（pointerup 之后浏览器还会补一个 click，
    //: 不吞就会在补线模式下顺手加一条切线）。
    const DRAG_CLICK_GUARD_MS = 300;
    // 下列四个常量必须与 mathbank/page_block_split.py 保持一致 —— 前端拿它们只做
    // 「别画出注定会被后端丢掉的框」和「预览残段」，规则的唯一真源仍然在后端：
    // 一个把手抖产生的小框画出来了、应用后却不见了的界面，比不画框更难查。
    const BOX_MIN_SIZE = 0.008;            // 与 MANUAL_BOX_MIN_SIZE 同值
    const BOX_OVERLAP_EPSILON = 0.002;     // 与 MANUAL_BOX_OVERLAP_EPSILON 同值
    const BOX_FRAGMENT_MIN_HEIGHT = 0.008; // 与 MANUAL_FRAGMENT_MIN_HEIGHT 同值
    const BOX_COLUMN_TOLERANCE = 0.01;     // 与 MANUAL_BOX_COLUMN_TOLERANCE 同值
    //: 判定「这条记录就是那个人工框生成的」用的容差（四边坐标逐个比对）。记录是后端
    //: 照着框直接生成的，四边本该全等；留容差是因为两边各自经过一次 round(6) 与浮点
    //: 折算。y 卡得紧（相邻框的间距远大于它），x 放得松 —— 框边会被吸附到分栏线，
    //: 而落库的 x 有效位数更少。
    const BOX_FOCUS_MATCH_TOLERANCE_Y = 0.004;
    const BOX_FOCUS_MATCH_TOLERANCE_X = 0.02;
    //: 双栏页上框边离分栏线多近就吸附过去。**不是**上面那个容差：后者是「判定这个框
    //: 算哪一栏」，这个是磁铁手感。0.02 在 600px 宽的预览上约 12px，落在手画框的正常
    //: 误差内，又不至于把「故意停在分栏线旁边」的框吸走。取 0.01 会不够用 ——
    //: 实测手滑溢出 1.05% 就会把邻栏整段切走，而那正是它要防的事。
    const BOX_SNAP_TOLERANCE = 0.02;
    //: 画框模式下停手多久就自动入库。1 秒是「画完一个框、看一眼右侧预览卡」的自然
    //: 停顿长度；更短会在连续画框时反复重建记录，更长则用户会以为没生效、又去点按钮。
    const BOX_AUTOSAVE_MS = 1000;
    //: ⌘Z 撤销栈深度。撤销的每一条都会重新走一遍后端重建（整页删旧插新），留太多
    //: 没有意义 —— 用户想回退到很远之前，直接重切一次更干净。
    const UNDO_LIMIT = 20;

    const state = {
        ready: false,
        students: [],
        subjects: [],
        batches: [],
        batch: null,
        records: [],
        pages: [],
        questionTypes: [],
        reasons: [],
        pageIndex: 0,
        zoom: 1,
        pageLayouts: {},         // { page_no: { mode, boundary } } 人工指定的栏目，覆盖自动判栏
        layoutDirty: {},         // { page_no: true } 栏目改过但还没按新栏目重切
        layoutDragUntil: 0,
        // 识别任务的实时队列：{active, queue:[id…], done:[id…], current:id|null}。
        // 由任务接口的 recognize_queue / recognize_current / recognize_done 驱动，
        // 前端只读它来画角标与只读锁，不据此切换当前选中的题。
        recognizeLive: { active: false, queue: [], done: [], current: null, errors: [] },
        boxMode: false,
        manualBoxes: {},         // { page_no: [[x0,y0,x1,y1], ...] } 人工框（本地工作副本）
        boxDirty: {},            // { page_no: true } 框改过但还没入库
        boxDraft: null,          // 正在拖出来的框（归一化），松手即入 manualBoxes
        boxSelected: null,       // 选中的框下标（用来加粗描边）
        boxDragUntil: 0,
        boxSaveTimer: null,      // 自动保存的防抖计时器
        boxSaving: false,        // 有一次入库请求正在飞
        boxSaveQueued: false,    // 请求飞行期间又改了，回来立刻再发一次
        boxSaveError: null,      // 上一次自动保存失败的原因（操作条上显示 + 可重试）
        boxSavedBody: {},        // { page_no: 请求体 JSON } 上次提交成功的内容，用来跳过空跑
        selectedRecordId: null,
        mergePick: [],           // 待合并的记录 id（顺序＝拼接顺序，先点的在上/在左）
        mergePending: null,      // 状态冲突时待确认的合并：{ records, primary }
        crossPageMerges: [],     // 批次级跨页合并组（成员带页号，见 mergeGroupOf）
        undoStack: [],           // ⌘Z 撤销栈：{ label, apply() }，上限 UNDO_LIMIT 条
        cardFilter: 'all',
        pendingFile: null,
        taskPoller: null,
        lastExport: null
    };

    // ---------------------------------------------------------------- 工具

    function el(id) { return document.getElementById(id); }

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function toast(message, type) {
        // 复用全局 toast 元素（api.js 的 showToast 是模块内私有函数，无法直接调用）
        const box = el('toast');
        const msg = el('toastMessage');
        const icon = el('toastIconContainer');
        if (!box || !msg) { console.log('[Mistake]', message); return; }
        msg.textContent = message;
        if (icon) {
            if (type === 'error') {
                icon.className = 'h-6 w-6 rounded-full flex items-center justify-center text-xs bg-red-500/20 text-red-400';
                icon.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i>';
            } else if (type === 'info') {
                icon.className = 'h-6 w-6 rounded-full flex items-center justify-center text-xs bg-brand-500/20 text-brand-500';
                icon.innerHTML = '<i class="fa-solid fa-circle-info"></i>';
            } else {
                icon.className = 'h-6 w-6 rounded-full flex items-center justify-center text-xs bg-green-500/20 text-green-400';
                icon.innerHTML = '<i class="fa-solid fa-circle-check"></i>';
            }
        }
        box.classList.remove('translate-y-12', 'opacity-0');
        box.classList.add('translate-y-0', 'opacity-100');
        clearTimeout(box.__mistakeTimer);
        box.__mistakeTimer = setTimeout(function () {
            box.classList.add('translate-y-12', 'opacity-0');
            box.classList.remove('translate-y-0', 'opacity-100');
        }, 3500);
    }

    function api(url, options) {
        // api.js 已给 window.fetch 打过补丁自动附加 X-Local-Token，这里保持普通 fetch
        return fetch(url, options).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                return { ok: response.ok, status: response.status, data: data };
            });
        });
    }

    function todayIso() {
        const now = new Date();
        const pad = function (value) { return String(value).padStart(2, '0'); };
        return now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
    }

    const MR_PREVIEW_FIGURE_CLASS = 'max-w-full max-h-40 rounded-lg border border-slate-200 dark:border-slate-700';

    /**
     * 把识别出来的题面转成可读预览。
     *
     * 正文 → HTML 的那一半（choices 环境、\fillin、\paren、折行、内联图）由
     * `MathRender.renderQuestionBody` 统一负责 —— 与重做录入页同一口径，
     * 否则同一道题两个工作台长得不一样。这里只管错题台特有的版式：
     * 每张图上挂移除按钮、缺图处插占位符芯片。
     *
     * 其余交给 KaTeX 的 auto-render 处理 `$...$`。刻意不做完整 Markdown 渲染 ——
     * 这里只是「让老师一眼看出识别对不对」，真正的编辑在 textarea 里。
     */
    function contentPreviewHtml(content, figures, placeholderTarget) {
        // 右上角挂个移除按钮 —— 补错了得能撤，不然只剩「去 textarea 里找那串 markdown」一条路。
        const inlineTarget = placeholderTarget === 'answer_images' ? 'answer_images' : 'figure_images';
        const figureList = Array.isArray(figures) ? figures : [];
        let html = window.MathRender.renderQuestionBody(content, {
            figureClass: MR_PREVIEW_FIGURE_CLASS,
            choiceClass: 'text-slate-700 dark:text-slate-200',
            // 占位符：能按出现顺序配到老 figure_images 的直接出图；配不到的说明还没补 ——
            // 渲染成一个**不可点**的琥珀色标记，只负责让人看见「这一处还缺图」。
            // 2026-09-18 起补图入口搬到了源码区（光标点到占位符上才浮出「补这张图」）：
            // 用户当时的注意力在源码里改字，预览里能点反而容易误触，而且「跳转」的发起位置
            // 本来就该在正在编辑的那一侧。
            beforeChoices: function (bodyHtml) {
                let cursor = 0;
                return bodyHtml.replace(/\[插图待补\s*[:：]\s*([^\]]+)\]/g, function (_match, label) {
                    const url = figureList[cursor];
                    if (url) {
                        cursor += 1;
                        return '<span class="block my-1.5"><img src="' + mrCropAttr(url) + '" alt="' + label +
                            '" class="' + MR_PREVIEW_FIGURE_CLASS + '">' +
                            '<span class="block text-[10px] text-slate-400 mt-0.5">' + label + '</span></span>';
                    }
                    return mrPlaceholderChipHtml(label);
                });
            },
            imageBuilder: function (ctx) {
                // 图片下方**不再**显示 markdown 的 alt（2026-09-18）：补图写进来的 alt 恒为「插图」，
                // 每张图底下挂一行灰字「插图」纯属噪音，用户看到还以为是占位符没替换干净。
                // alt 仍留在 <img alt> 属性里（无障碍、图片加载失败时有用），只是不当标注渲染。
                return '<span class="relative inline-block my-1.5"><img src="' + mrCropAttr(ctx.safeUrl) +
                    '" alt="' + mrCropAttr(ctx.alt) + '" class="' + MR_PREVIEW_FIGURE_CLASS + '">' +
                    '<button type="button" title="从正文里移除这张图"' +
                    ' onclick="removeInlineMistakeImage(\'' + inlineTarget + '\',' + ctx.index + ')"' +
                    ' class="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-600 text-slate-500 text-[10px] leading-none flex items-center justify-center">' +
                    '<i class="fa-solid fa-xmark text-[9px]"></i></button></span>';
            }
        }).html;
        return html;
    }

    function renderMathIn(container) {
        window.MathRender.render(container, 'display');
    }

    // ---------------------------------------------------------------- 任务轮询

    function stopTaskPoll() {
        if (state.taskPoller) {
            clearInterval(state.taskPoller);
            state.taskPoller = null;
        }
    }

    function renderStepBar(steps) {
        const bar = el('mistakeStepBar');
        if (!bar) return;
        if (!steps || !steps.length) {
            bar.classList.add('hidden');
            bar.innerHTML = '';
            return;
        }
        bar.classList.remove('hidden');
        bar.classList.add('flex');
        bar.innerHTML = steps.map(function (step, index) {
            const status = step.status || 'pending';
            let ring = 'bg-white text-slate-400 border-slate-300';
            let icon = String(index + 1);
            let text = 'text-slate-400';
            if (status === 'done') { ring = 'bg-emerald-500 text-white border-emerald-500'; icon = '<i class="fa-solid fa-check"></i>'; text = 'text-emerald-600'; }
            else if (status === 'active') { ring = 'bg-brand-600 text-white border-brand-600'; icon = '<i class="fa-solid fa-circle-notch fa-spin"></i>'; text = 'text-brand-700 font-semibold'; }
            else if (status === 'error') { ring = 'bg-red-500 text-white border-red-500'; icon = '<i class="fa-solid fa-xmark"></i>'; text = 'text-red-600 font-semibold'; }
            const isLast = index === steps.length - 1;
            const connector = isLast ? '' :
                '<div class="flex-1 h-0.5 mt-2.5 ' + (status === 'done' ? 'bg-emerald-400' : 'bg-slate-200') + '"></div>';
            const detail = step.detail ?
                '<span class="text-[9px] text-slate-500 mt-0.5 block max-w-[80px] truncate" title="' + esc(step.detail) + '">' + esc(step.detail) + '</span>' : '';
            return '<div class="flex items-start ' + (isLast ? '' : 'flex-1') + '">' +
                '<div class="flex flex-col items-center shrink-0 w-[76px]">' +
                    '<div class="w-5 h-5 rounded-full border flex items-center justify-center text-[9px] ' + ring + '">' + icon + '</div>' +
                    '<span class="text-[10px] mt-1 text-center ' + text + '">' + esc(step.label || '') + '</span>' +
                    detail +
                '</div>' + connector + '</div>';
        }).join('');
    }

    function setTaskBar(text, kind) {
        const bar = el('mistakeTaskBar');
        const icon = el('mistakeTaskIcon');
        const textEl = el('mistakeTaskText');
        if (!bar || !textEl) return;
        if (!text) {
            bar.classList.add('hidden');
            return;
        }
        bar.classList.remove('hidden');
        textEl.textContent = text;
        if (icon) {
            icon.className = kind === 'error'
                ? 'fa-solid fa-triangle-exclamation text-red-500'
                : (kind === 'done' ? 'fa-solid fa-circle-check text-emerald-500'
                    : 'fa-solid fa-circle-notch fa-spin text-brand-600');
        }
    }

    /**
     * 轮询异步任务。
     *
     * onDone 收到终态任务对象；轮询期间同步刷新步骤条与进度文案。失败/取消也走 onDone ——
     * 让调用方在一处决定「成功后干什么、失败后提示什么」，避免两套分支。
     */
    function pollTask(taskId, options) {
        const opts = options || {};
        stopTaskPoll();
        let ticks = 0;
        const tick = function () {
            api('/api/tasks/' + encodeURIComponent(taskId) + '/status').then(function (res) {
                const task = res.data || {};
                if (task.steps) renderStepBar(task.steps);
                if (task.status === 'completed' || task.status === 'error' || task.status === 'cancelled') {
                    stopTaskPoll();
                    if (task.error) setTaskBar(task.error, 'error');
                    else setTaskBar(task.log || '任务完成。', 'done');
                    if (opts.onDone) opts.onDone(task);
                    return;
                }
                // 逐题出结果的任务（识别）靠这个钩子把中间结果推到界面上：
                // 只在终态回调一次的话，用户得等整批跑完才看得到第一道题。
                if (opts.onProgress) {
                    try { opts.onProgress(task); } catch (error) { /* 钩子异常不能中断轮询 */ }
                }
                ticks += 1;
                const progress = typeof task.progress === 'number' ? task.progress : 0;
                setTaskBar((task.log || '任务执行中…') + '（' + progress + '%）', 'running');
                if (ticks > (opts.maxTicks || 900)) {
                    stopTaskPoll();
                    setTaskBar('等待超时：任务可能仍在后台执行，可稍后刷新查看。', 'error');
                    if (opts.onDone) opts.onDone({ status: 'timeout' });
                }
            }).catch(function () { /* 单次网络抖动不终止轮询 */ });
        };
        tick();
        state.taskPoller = setInterval(tick, 1000);
    }

    // ---------------------------------------------------------------- 批次列表

    function renderBatchList() {
        const list = el('mistakeBatchList');
        if (!list) return;
        const summary = el('mistakeListSummary');
        if (summary) {
            const totals = state.batches.reduce(function (acc, batch) {
                acc.pages += batch.page_count || 0;
                acc.records += batch.total || 0;
                acc.incorrect += batch.incorrect || 0;
                return acc;
            }, { pages: 0, records: 0, incorrect: 0 });
            summary.textContent = state.batches.length
                ? (state.batches.length + ' 个批次 · ' + totals.pages + ' 页 · ' +
                    totals.records + ' 题 · ' + totals.incorrect + ' 错')
                : '还没有批次，先新建一个吧';
        }
        if (!state.batches.length) {
            list.innerHTML = '<div class="text-center text-xs text-slate-400 py-16">' +
                '<i class="fa-solid fa-inbox text-2xl block mb-2 opacity-40"></i>' +
                '点右上角「新建批次」上传扫描件，系统会先切题、再让你逐题点选错题。</div>';
            return;
        }
        list.innerHTML = state.batches.map(function (batch) {
            const subject = SUBJECT_LABELS[batch.subject] || batch.subject;
            const statusLabel = STATUS_LABELS[batch.status] || batch.status;
            const statusCls = batch.status === 'cut_failed' ? 'bg-red-50 text-red-600 border-red-200'
                : batch.status === 'done' ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                    : 'bg-slate-100 text-slate-500 border-slate-200';
            const progressHint = batch.total
                ? (batch.recognized + '/' + batch.incorrect + ' 错题已识别')
                : (batch.page_count ? '待点选' : '待切题');
            return '<div class="glass-card rounded-xl px-4 py-3 flex items-center justify-between gap-3 hover-lift cursor-pointer" onclick="openMistakeBatch(' + batch.id + ')">' +
                '<div class="min-w-0 flex-1">' +
                    '<div class="flex items-center gap-2">' +
                        '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-200 font-semibold border border-brand-200/60">' + esc(subject) + '</span>' +
                        '<span class="font-semibold text-sm truncate">' + esc(batch.title || '未命名批次') + '</span>' +
                    '</div>' +
                    '<div class="mt-1 text-[11px] text-slate-500 flex items-center gap-2 flex-wrap">' +
                        '<span>' + (batch.batch_date || '—') + '</span>' +
                        '<span class="text-slate-300">|</span>' +
                        '<span>' + (batch.page_count || 0) + ' 页 · ' + (batch.total || 0) + ' 题</span>' +
                        (batch.incorrect ? '<span class="text-red-600 font-semibold">' + batch.incorrect + ' 错</span>' : '') +
                        (batch.imported ? '<span class="text-emerald-600">已入库 ' + batch.imported + '</span>' : '') +
                        '<span>' + esc(progressHint) + '</span>' +
                    '</div>' +
                '</div>' +
                '<div class="flex items-center gap-2 shrink-0">' +
                    '<span class="text-[10px] px-2 py-1 rounded-lg border ' + statusCls + '">' + esc(statusLabel) + '</span>' +
                    '<button type="button" class="glass-btn px-2.5 py-1.5 text-[11px] font-semibold" onclick="event.stopPropagation();openMistakeBatch(' + batch.id + ')">继续</button>' +
                    '<button type="button" class="glass-btn w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-red-500" title="删除批次" onclick="event.stopPropagation();deleteMistakeBatch(' + batch.id + ')"><i class="fa-solid fa-trash text-[10px]"></i></button>' +
                '</div>' +
            '</div>';
        }).join('');
    }

    function loadMistakeBatches() {
        return api('/api/mistakes/batches?page=1&page_size=100').then(function (res) {
            if (!res.ok) { toast(res.data.message || '批次列表加载失败', 'error'); return; }
            state.batches = res.data.batches || [];
            state.students = res.data.students || state.students;
            state.subjects = res.data.subjects || state.subjects;
            renderBatchList();
            renderStudentChip();
            fillSubjectOptions();
        });
    }

    function renderStudentChip() {
        const chip = el('mistakeStudentChip');
        if (!chip) return;
        const student = state.students[0];
        chip.textContent = student ? ('学生：' + student.name + (student.grade ? ' · ' + student.grade : '')) : '学生：—';
        const nameInput = el('mistakeStudentNameInput');
        const gradeInput = el('mistakeStudentGradeInput');
        if (student && nameInput && !nameInput.value) nameInput.value = student.name;
        if (student && gradeInput && !gradeInput.value) gradeInput.value = student.grade || '';
    }

    function fillSubjectOptions() {
        const select = el('mistakeSubjectInput');
        if (!select || select.dataset.filled === '1') return;
        const subjects = state.subjects.length ? state.subjects
            : Object.keys(SUBJECT_LABELS).map(function (key) { return { value: key, label: SUBJECT_LABELS[key] }; });
        select.innerHTML = subjects.map(function (item) {
            return '<option value="' + esc(item.value) + '">' + esc(item.label) + '</option>';
        }).join('');
        select.dataset.filled = '1';
    }

    // ---------------------------------------------------------------- 新建批次

    function toggleMistakeNewBatch(force) {
        const panel = el('mistakeNewBatchPanel');
        if (!panel) return;
        const shouldOpen = typeof force === 'boolean' ? force : panel.classList.contains('hidden');
        panel.classList.toggle('hidden', !shouldOpen);
        if (shouldOpen && !el('mistakeDateInput').value) el('mistakeDateInput').value = todayIso();
    }

    function bindDropZone() {
        const zone = el('mistakeDropZone');
        const input = el('mistakeFileInput');
        if (!zone || !input) return;
        zone.addEventListener('click', function () { input.click(); });
        input.addEventListener('change', function () {
            if (input.files && input.files[0]) setPendingFile(input.files[0]);
        });
        ['dragenter', 'dragover'].forEach(function (eventName) {
            zone.addEventListener(eventName, function (event) {
                event.preventDefault();
                zone.classList.add('border-brand-500', 'bg-brand-50/40');
            });
        });
        ['dragleave', 'drop'].forEach(function (eventName) {
            zone.addEventListener(eventName, function (event) {
                event.preventDefault();
                zone.classList.remove('border-brand-500', 'bg-brand-50/40');
            });
        });
        zone.addEventListener('drop', function (event) {
            const files = event.dataTransfer && event.dataTransfer.files;
            if (files && files[0]) setPendingFile(files[0]);
        });
    }

    function setPendingFile(file) {
        state.pendingFile = file;
        const hint = el('mistakeFileHint');
        if (hint) {
            hint.classList.remove('hidden');
            hint.textContent = '已选择：' + file.name + '（' + (file.size / 1024 / 1024).toFixed(2) + ' MB）';
        }
    }

    function submitMistakeBatch() {
        if (!state.pendingFile) { toast('请先选择要上传的扫描件', 'error'); return; }
        const button = el('mistakeCreateBtn');
        if (button) { button.disabled = true; button.textContent = '正在上传…'; }
        const form = new FormData();
        form.append('file', state.pendingFile, state.pendingFile.name);
        form.append('subject', el('mistakeSubjectInput').value || 'math');
        form.append('title', el('mistakeTitleInput').value || '');
        form.append('batch_date', el('mistakeDateInput').value || todayIso());
        form.append('student_name', el('mistakeStudentNameInput').value || '');
        form.append('student_grade', el('mistakeStudentGradeInput').value || '');

        api('/api/mistakes/batches', { method: 'POST', body: form }).then(function (res) {
            if (button) { button.disabled = false; button.textContent = '上传并开始切题'; }
            if (!res.ok || !res.data.batch) {
                toast(res.data.message || '新建批次失败', 'error');
                return;
            }
            state.pendingFile = null;
            const hint = el('mistakeFileHint');
            if (hint) hint.classList.add('hidden');
            const fileInput = el('mistakeFileInput');
            if (fileInput) fileInput.value = '';
            el('mistakeTitleInput').value = '';
            toggleMistakeNewBatch(false);
            toast('批次已建立，正在切题…', 'info');
            loadMistakeBatches().then(function () {
                openMistakeBatch(res.data.batch.id, true);
            });
        }).catch(function (error) {
            if (button) { button.disabled = false; button.textContent = '上传并开始切题'; }
            toast('上传失败：' + error.message, 'error');
        });
    }

    function deleteMistakeBatch(batchId) {
        if (!window.confirm('删除该批次会连同已切好的题块、识别结果一起删掉，确定吗？')) return;
        api('/api/mistakes/batches/' + batchId, { method: 'DELETE' }).then(function (res) {
            if (!res.ok) { toast(res.data.message || '删除失败', 'error'); return; }
            toast('批次已删除');
            // 批次没了，它记的停留页也没意义了，顺手清掉免得 localStorage 里攒垃圾
            forgetPageIndex(batchId);
            if (state.batch && state.batch.id === batchId) backToMistakeList();
            loadMistakeBatches();
        });
    }

    // ---------------------------------------------------------------- 批次详情

    function openMistakeBatch(batchId, autoCut) {
        const listView = el('mistakeListView');
        const detailView = el('mistakeDetailView');
        if (listView) listView.classList.add('hidden');
        if (detailView) detailView.classList.remove('hidden');
        state.selectedRecordId = null;
        state.pageLayouts = {};
        state.layoutDirty = {};
        state.manualBoxes = {};
        state.boxDirty = {};
        state.boxDraft = null;
        state.boxSelected = null;
        state.boxMode = false;
        cancelBoxSaveTimer();
        state.boxSavedBody = {};
        state.boxSaveError = null;
        state.mergePick = [];
        state.mergePending = null;
        state.crossPageMerges = [];
        state.undoStack = [];
        state.pageFlashId = null;
        // 回到上次离开时那一页（按批次各记一份）；越界交给 applyBatchDetail 兜底收拢
        state.pageIndex = savedPageIndexOf(batchId);
        closeMergeDialog();
        renderStepBar(null);
        setTaskBar('');
        api('/api/mistakes/batches/' + batchId).then(function (res) {
            if (!res.ok) { toast(res.data.message || '批次详情加载失败', 'error'); backToMistakeList(); return; }
            applyBatchDetail(res.data);
            if (autoCut || !state.pages.length) startMistakeCut();
        });
    }

    function applyBatchDetail(payload) {
        state.batch = payload.batch || null;
        state.records = payload.records || [];
        state.pages = payload.pages || [];
        // 跨页合并组挂在批次顶层（成员带页号），不由任何一页承载
        state.crossPageMerges = Array.isArray(payload.cross_page_merges)
            ? payload.cross_page_merges : [];
        state.questionTypes = payload.question_types || [];
        state.difficulties = payload.difficulties || [];
        state.reasons = payload.mistake_reasons || [];
        if (payload.student) state.students = [payload.student];
        if (state.pageIndex >= state.pages.length) {
            // 重切后页数变少（或记下的页号本就超界）：收拢到最后一页，并把修正后的值落盘
            state.pageIndex = Math.max(0, state.pages.length - 1);
            savePageIndex();
        }
        syncLayoutState();
        syncBoxState();
        renderDetailHeader();
        renderPageView();
        renderCards();
        renderStats();
        renderCardFilters();
    }

    /**
     * 把服务端记住的人工栏目读回本地状态。
     *
     * 重切后 `layout.source` 会是 `manual`，分栏线是后端按新栏目自动补的真实位置 ——
     * 不读回来，前端会一直画着拖之前那条线。还没重切的页（layoutDirty）跳过，
     * 否则一次刷新就把用户刚拖好、还没提交的分栏线抹掉了。
     */
    function syncLayoutState() {
        state.pages.forEach(function (page) {
            if (state.layoutDirty[page.page_no]) return;
            const layout = page.layout || {};
            if (layout.source !== 'manual') {
                delete state.pageLayouts[page.page_no];
                return;
            }
            const boundary = Number(page.column_boundary);
            if (layout.mode === 'double') {
                state.pageLayouts[page.page_no] = (boundary > 0 && boundary < 1)
                    ? { mode: 'double', boundary: boundary }
                    : { mode: 'double' };
            } else {
                state.pageLayouts[page.page_no] = { mode: 'single' };
            }
        });
    }

    /**
     * 把服务端记住的人工框读回本地。
     *
     * 与栏目同理：还没提交的页（boxDirty）跳过，否则一次刷新就把用户
     * 刚框好、还没提交的框抹掉了。没被点名的页严格以服务端为准 —— 别处（命令行
     * 工具、另一个窗口）改过框时，本地缓存必须让位。
     */
    function syncBoxState() {
        state.pages.forEach(function (page) {
            if (state.boxDirty[page.page_no]) return;
            const boxes = Array.isArray(page.manual_boxes) ? page.manual_boxes : [];
            state.manualBoxes[page.page_no] = boxes.map(function (box) {
                return [Number(box[0]), Number(box[1]), Number(box[2]), Number(box[3])];
            });
        });
    }

    /**
     * 拉回批次详情。
     *
     * lightweight 只换 records 数据，不重绘详情页。识别期间每秒全量重绘一次题卡与
     * 页面图纯属浪费（几十个元素的 innerHTML 重建），而且重建会把详情页列表的滚动
     * 位置清零 —— 用户从审校页返回时会莫名其妙回到顶部。返回时补一次全量即可。
     */
    function refreshMistakeDetail(options) {
        if (!state.batch) return Promise.resolve();
        const light = !!(options && options.lightweight);
        return api('/api/mistakes/batches/' + state.batch.id).then(function (res) {
            if (!res.ok) return;
            if (light) {
                const payload = res.data || {};
                state.batch = payload.batch || state.batch;
                state.records = payload.records || [];
                updateRecognizeButton();
                return;
            }
            applyBatchDetail(res.data);
        });
    }

    function renderDetailHeader() {
        const batch = state.batch;
        if (!batch) return;
        const title = el('mistakeDetailTitle');
        const meta = el('mistakeDetailMeta');
        if (title) title.textContent = (SUBJECT_LABELS[batch.subject] || batch.subject) + ' · ' + (batch.title || '未命名批次');
        if (meta) {
            meta.textContent = [
                batch.batch_date || '—',
                (batch.page_count || 0) + ' 页',
                (batch.total || 0) + ' 题',
                STATUS_LABELS[batch.status] || batch.status
            ].join(' · ');
        }
    }

    function backToMistakeList() {
        stopTaskPoll();
        // 离开批次前把本页攒着的框送出去（同上：页一没，本地那份就没有落点）
        cancelBoxSaveTimer();
        flushMistakeBoxSave();
        const listView = el('mistakeListView');
        const detailView = el('mistakeDetailView');
        if (detailView) detailView.classList.add('hidden');
        if (listView) listView.classList.remove('hidden');
        state.batch = null;
        loadMistakeBatches();
    }

    function currentPage() {
        return state.pages[state.pageIndex] || null;
    }

    function recordsOnPage(pageNo) {
        return state.records.filter(function (record) { return record.page_no === pageNo; });
    }

    // ---------------------------------------------------------------- 栏结构
    //
    // 一页可能是双栏（左右各半，各切各的），也可能是通栏。自动判栏会出错，所以：
    // 1) 每页顶部有「自动 / 单栏 / 双栏」三态开关，人工指定优先；
    // 2) 双栏时页面上画一条分栏线，可以直接拖；
    // 3) 改动只作用于「栏目」这一层，题块要等用户点「按新栏目重切」才重算 ——
    //    拖动时分栏线实时跟手，但不在中途反复重切（会很慢，也看不清结果）。

    /** 本页生效的栏结构：人工指定优先，否则用自动判栏结果。 */
    function effectiveLayout(page) {
        const manual = state.pageLayouts[page.page_no];
        const auto = page.layout || {};
        if (manual && manual.mode === 'single') {
            return { mode: 'single', isDouble: false, boundary: 1, source: 'manual', confidence: 1 };
        }
        if (manual && manual.mode === 'double') {
            const boundary = Number(manual.boundary);
            const fallback = Number(page.column_boundary);
            return {
                mode: 'double',
                isDouble: true,
                source: 'manual',
                confidence: 1,
                // 人工只表态「双栏」还没拖过分栏线时，先拿自动检测到的缝占位；
                // 自动也没给（单栏页）就画在正中 —— 后端重切时会自己补一条真线。
                boundary: (boundary > 0 && boundary < 1)
                    ? boundary
                    : ((fallback > 0 && fallback < 1) ? fallback : DEFAULT_COLUMN_BOUNDARY)
            };
        }
        const autoBoundary = Number(auto.boundary);
        const isDouble = auto.mode === 'double' && autoBoundary > 0 && autoBoundary < 1;
        return {
            mode: isDouble ? 'double' : 'single',
            isDouble: isDouble,
            boundary: isDouble ? autoBoundary : 1,
            source: auto.source || 'assumed',
            confidence: Number(auto.confidence == null ? 1 : auto.confidence)
        };
    }

    /** 题块所属栏号：双栏页 1 = 左栏、2 = 右栏；单栏页恒为 0。 */
    function columnOf(layout, record) {
        if (!layout.isDouble) return 0;
        if (record.column_index === 1 || record.column_index === 2) return record.column_index;
        // 老记录没有 column_index，用横向中点兜底
        const start = Number(record.block_x_start == null ? 0 : record.block_x_start);
        const end = Number(record.block_x_end == null ? 1 : record.block_x_end);
        return (start + end) / 2 < layout.boundary ? 1 : 2;
    }

    function columnLabel(column) {
        return column === 1 ? '左栏' : (column === 2 ? '右栏' : '');
    }

    /** 人工栏目的请求体：只带用户改过的页，其余页沿用自动判栏。 */
    function layoutPayload() {
        const payload = {};
        Object.keys(state.pageLayouts).forEach(function (key) {
            const item = state.pageLayouts[key] || {};
            const mode = item.mode;
            if (mode !== 'single' && mode !== 'double') return;
            const boundary = Number(item.boundary);
            if (mode === 'double' && boundary > 0 && boundary < 1) {
                payload[key] = { mode: 'double', boundary: Number(boundary.toFixed(4)) };
            } else {
                payload[key] = mode;
            }
        });
        return payload;
    }

    // ---------------------------------------------------------------- 人工框选
    //
    // 自动切块在密排页上会切得过细（一行一块），逐个手工纠错的成本比整块框一遍还高。
    // 所以给一条人工通道：在页图上拖矩形，框内单独成块。
    //
    // 与自动块的关系是「遮蔽」而不是「替换」：
    // * 人工框另存一份（服务端 manual_boxes），不写回自动 blocks；
    // * 应用时后端把原自动块里被框压住的纵向区间减掉，**没被压到的碎片原样留着** ——
    //   框到哪，哪才归框，不会因为框压住半块就把整块吞掉（整块吞掉等于悄悄丢字）；
    // * 框清空即精确回到纯自动结果。
    // 前端这边只负责「画出来」和「把框送过去」，切分规则一律以后端为准。
    //
    // 左侧的残段预览是按上面同一套规则在 JS 里复算的（见 survivingSpans）：不预览的话，
    // 用户框住半块、应用后突然多出一个碎片卡片，就成了「怎么又切这么细」的第二次复现。

    /** 本页的人工框（本地工作副本，惰性初始化）。 */
    function boxesFor(pageNo) {
        if (!Array.isArray(state.manualBoxes[pageNo])) state.manualBoxes[pageNo] = [];
        return state.manualBoxes[pageNo];
    }

    /** 把两个角点整成 (x0≤x1, y0≤y1) 并夹在页内。 */
    function normalizeBox(x0, y0, x1, y1) {
        return [
            Math.max(0, Math.min(x0, x1)),
            Math.max(0, Math.min(y0, y1)),
            Math.min(1, Math.max(x0, x1)),
            Math.min(1, Math.max(y0, y1))
        ];
    }

    /** 是否小到注定被后端 clean_manual_boxes 丢掉。 */
    function boxTooSmall(box) {
        return (box[2] - box[0]) < BOX_MIN_SIZE || (box[3] - box[1]) < BOX_MIN_SIZE;
    }

    /** 画框模式的请求体：一页一次，空数组＝清空本页框。 */
    function boxesPayloadFor(page) {
        return {
            boxes: boxesFor(page.page_no).map(function (box) { return box.slice(); })
        };
    }

    /** 框会落进哪一栏（双栏页；跨栏或无分栏线时为 0）—— 与后端 boxes_to_blocks 同规则。 */
    function columnOfBox(layout, box) {
        if (!layout.isDouble) return 0;
        if (box[2] <= layout.boundary + BOX_COLUMN_TOLERANCE) return 1;
        if (box[0] >= layout.boundary - BOX_COLUMN_TOLERANCE) return 2;
        return 0;
    }

    /**
     * 双栏页上，把「差一点点就贴到分栏线」的框边吸附到分栏线。
     *
     * 不吸附会出一个很安静的坏事：右栏的框往左溢出 1%（肉眼看不出来），后端按
     * 「框到哪，哪就归框」会把左栏那段纵向区间一并切走 —— 左栏的题被削掉一截，
     * 而页面上只是框稍微宽了点。吸附之后的框边正好落在分栏线上，横向重叠为 0，
     * 邻栏从根上碰不到。
     *
     * 故意跨栏的框不会被吸走：只有框边落在分栏线 ±BOX_SNAP_TOLERANCE 内才吸附。
     */
    function snapBoxToColumn(box, layout) {
        if (!layout || !layout.isDouble) return box;
        const boundary = layout.boundary;
        const snapped = box.slice();
        if (Math.abs(snapped[0] - boundary) <= BOX_SNAP_TOLERANCE) snapped[0] = boundary;
        if (Math.abs(snapped[2] - boundary) <= BOX_SNAP_TOLERANCE) snapped[2] = boundary;
        return snapped;
    }

    /** 该框在横向上是否压到了这个题块 —— 压不到就谈不上切它。 */
    function boxOverlapsRecord(box, record) {
        const start = Math.max(0, Number(record.block_x_start == null ? 0 : record.block_x_start));
        const end = Math.min(1, Number(record.block_x_end == null ? 1 : record.block_x_end));
        return (Math.min(box[2], end) - Math.max(box[0], start)) > BOX_OVERLAP_EPSILON;
    }

    /**
     * 复算某个自动块被这些框切完剩下的纵向区间（对应后端 _subtract_manual_boxes）。
     *
     * 返回 `[[yStart, yEnd], …]`，全是原块范围内的残段，按 y 升序；返回空数组＝
     * 整块被框吃掉。只做纯区间运算，不碰 DOM，方便单独测。
     */
    function survivingSpans(record, boxes) {
        return survivingSpansIn(
            Math.max(0, Number(record.block_x_start == null ? 0 : record.block_x_start)),
            Math.min(1, Number(record.block_x_end == null ? 1 : record.block_x_end)),
            Number(record.block_y_start),
            Number(record.block_y_end),
            boxes
        );
    }

    /**
     * 同 survivingSpans 的区间运算，但矩形由调用方给。
     *
     * 合并题得按**每个成员矩形**各算一份残段：成员各自都可能被框压住，只传 record
     * 进去只能拿到并集那一个范围，切出来的残段跟实际不符（并集还会把不相干的区段
     * 一并算成「被切到」）。
     */
    function survivingSpansIn(x0, x1, y0, y1, boxes) {
        const spans = (boxes || [])
            .filter(function (box) {
                return (Math.min(box[2], x1) - Math.max(box[0], x0)) > BOX_OVERLAP_EPSILON;
            })
            .map(function (box) { return [Math.max(y0, box[1]), Math.min(y1, box[3])]; })
            .filter(function (span) { return span[1] - span[0] > 0; });
        if (!spans.length) return [[y0, y1]];
        let remaining = [[y0, y1]];
        spans.forEach(function (span) {
            const pieces = [];
            remaining.forEach(function (piece) {
                if (span[1] <= piece[0] || span[0] >= piece[1]) { pieces.push(piece); return; }
                if (span[0] > piece[0]) pieces.push([piece[0], span[0]]);
                if (span[1] < piece[1]) pieces.push([span[1], piece[1]]);
            });
            remaining = pieces;
        });
        return remaining.filter(function (piece) {
            return piece[1] - piece[0] >= BOX_FRAGMENT_MIN_HEIGHT;
        });
    }

    function syncBoxModeButton() {
        const button = el('mistakeBoxModeBtn');
        if (!button) return;
        button.classList.toggle('glass-btn-primary', state.boxMode);
        button.innerHTML = '<i class="fa-solid fa-vector-square text-[10px]"></i><span>' +
            (state.boxMode ? '退出画框' : '画框') + '</span>';
    }

    function toggleMistakeBoxMode() {
        const leaving = state.boxMode;
        state.boxMode = !state.boxMode;
        if (leaving) {
            // 退出画框模式时把攒着的改动立刻送出去，而不是连着计时器一起丢掉 ——
            // 用户是「画完就去编辑题目」，此刻他认定这些框已经存下了
            state.boxDraft = null;
            cancelBoxSaveTimer();
            flushMistakeBoxSave();
        }
        syncBoxModeButton();
        renderPageView();
        renderCards();
    }

    /** 记一个框（拖完松手时调用）。太小的直接丢，连提示都不给 —— 那只是手抖。 */
    function addMistakeBox(box) {
        const page = currentPage();
        if (!page) return;
        // 先吸附再判尺寸：吸附可能把框宽压到 0（贴着分栏线画的一条细缝），
        // 那种框本来就是无效的，顺序反了就会把它当成有效框存下来
        const snapped = snapBoxToColumn(box, effectiveLayout(page));
        if (!boxTooSmall(snapped)) {
            const boxes = boxesFor(page.page_no);
            boxes.push(snapped);
            state.boxSelected = boxes.length - 1;
            markPageBoxesDirty(page);
            return;
        }
        // 没被收下也要重绘：草框已经从 state.boxDraft 清掉了，不重绘会留在屏上
        renderPageView();
        renderCards();
    }

    function removeMistakeBox(index) {
        const page = currentPage();
        if (!page) return;
        const boxes = boxesFor(page.page_no);
        if (index < 0 || index >= boxes.length) return;
        boxes.splice(index, 1);
        state.boxSelected = null;
        markPageBoxesDirty(page);
    }

    /** 清空本页框：清成空数组并标脏，交给自动保存（或「立即保存」）提交。 */
    function clearMistakePageBoxes() {
        const page = currentPage();
        if (!page) return;
        const count = boxesFor(page.page_no).length;
        if (!count) { toast('本页还没有框'); return; }
        state.manualBoxes[page.page_no] = [];
        state.boxSelected = null;
        state.boxDraft = null;
        markPageBoxesDirty(page);
        toast('已清空本页 ' + count + ' 个框');
    }

    /** 框集合变了之后的统一收尾：标脏 + 重绘 + 排队自动保存。 */
    function markPageBoxesDirty(page) {
        state.boxDirty[page.page_no] = true;
        state.boxSaveError = null;
        renderPageView();
        renderCards();
        scheduleMistakeBoxSave();
    }

    function cancelBoxSaveTimer() {
        if (state.boxSaveTimer) {
            clearTimeout(state.boxSaveTimer);
            state.boxSaveTimer = null;
        }
    }

    /**
     * 现在能不能把本页的框提交给后端；不能的话返回原因（显示在操作条上）。
     *
     * 自动保存没有「用户又点了一次按钮」这个重新决策的机会，所以拦下来的理由必须
     * 写在界面上 —— 只说「失败」的话，用户会以为框丢了、又重画一遍。
     */
    function boxSaveBlocker(page) {
        // 栏目改了还没重切时不许提交：框是照着「新的分栏线」画的，后端却还在用
        // 旧的 column_boundary 归属栏号与排序，结果会张冠李戴。先重切再框。
        if (state.layoutDirty[page.page_no]) {
            return '本页栏目改过还没重切，先点「按新栏目重切」再框选';
        }
        const boxes = boxesFor(page.page_no);
        // 有框、但一个都没通过尺寸校验：后端会原样当成「清空」，静默清掉一堆框
        // 比报错更糟，所以这里先拦下来。空列表是用户主动清空，放行。
        if (boxes.length && boxes.every(boxTooSmall)) {
            return '这些框太小了，先框大一点';
        }
        return '';
    }

    /**
     * 停手 BOX_AUTOSAVE_MS 后把本页的框提交给后端。
     *
     * **只在画框模式下生效**。这是个有意的边界：退出画框模式后，用户是在右侧卡片上
     * 填错因、写题面，此时若还藏着一次自动保存，列表会在他打字的中途整体重建，
     * 输入框失焦、刚敲的字没了。「画框 = 切分题目」「退出画框 = 编辑题目」两条时间线
     * 分开，各自都不会打断对方。
     */
    function scheduleMistakeBoxSave() {
        cancelBoxSaveTimer();
        if (!state.boxMode) return;
        const page = currentPage();
        if (!page || !state.boxDirty[page.page_no]) return;
        state.boxSaveTimer = setTimeout(function () {
            state.boxSaveTimer = null;
            flushMistakeBoxSave();
        }, BOX_AUTOSAVE_MS);
    }

    /**
     * 真正提交本页的框 —— 自动保存与「立即保存」共用这一条路径。
     *
     * 三个约束：
     * - **串行**：请求飞行期间又改了，就地记一笔「还欠一次」，等回来再发。并发两个
     *   POST 会让后端按到达顺序全量重建两遍记录，慢的那个后到就会把新结果盖回去。
     * - **幂等**：请求体和上次提交成功的一模一样就不发 —— 画框时用户常常改回去，
     *   白跑一趟就是白重建一遍记录、白重裁十张图。
     * - **失败不静默**：保留本地框、保持标脏、把原因摆在操作条上并给重试入口。静默
     *   丢掉的框用户在页面上看不出来，等到导出讲义时才发现少了一道题。
     */
    function flushMistakeBoxSave() {
        const page = currentPage();
        if (!page || !state.batch) return;
        if (!state.boxDirty[page.page_no]) return;
        const blocked = boxSaveBlocker(page);
        if (blocked) {
            state.boxSaveError = blocked;
            renderBoxBar(page);
            return;
        }
        if (state.boxSaving) { state.boxSaveQueued = true; return; }

        const boxNo = page.page_no;
        const body = JSON.stringify(boxesPayloadFor(page));
        if (body === state.boxSavedBody[boxNo]) {
            // 改完又改回去了，和库里那份一模一样：不必再跑一遍
            delete state.boxDirty[boxNo];
            state.boxSaveError = null;
            renderBoxBar(page);
            renderCards();
            return;
        }
        state.boxSaving = true;
        state.boxSaveError = null;
        renderBoxBar(page);
        const button = el('mistakeBoxApplyBtn');
        if (button) { button.disabled = true; button.textContent = '保存中…'; }
        api('/api/mistakes/batches/' + state.batch.id + '/pages/' + boxNo + '/blocks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body
        }).then(function (res) {
            state.boxSaving = false;
            if (button) { button.disabled = false; button.textContent = '立即保存'; }
            // 请求期间用户可能翻到别的页了，这时 currentPage() 已经不是这一页，
            // 别拿它的状态去改别人的操作条
            const still = currentPage();
            const samePage = !!still && still.page_no === boxNo;
            if (!res.ok) {
                state.boxSaveError = res.data.message || '自动保存失败';
                if (samePage) { renderBoxBar(still); renderCards(); }
                toast(state.boxSaveError + '（框还留着，可点「立即保存」重试）', 'error');
                return;
            }
            state.boxSavedBody[boxNo] = body;
            state.boxSaveError = null;
            // 脏标记的语义是「本地这一页和库里那份不一样」，不是「有过一次没回来的请求」。
            // 所以只有「提交之后没人再动过」才敢收掉它 —— 请求飞行期间用户又画了一个框
            // 的话，那个框还没入库，标记一收就再也没人排队保存它了（自动保存只在标脏
            // 时才排队），而界面上它好端端地画着，看不出来没存。
            const freshBody = samePage ? JSON.stringify(boxesPayloadFor(still)) : null;
            if (freshBody === body) {
                delete state.boxDirty[boxNo];
            } else {
                state.boxSaveQueued = true;   // 还有一版没送，回来立刻补
            }
            if (samePage) {
                renderBoxBar(still);
                renderCards();
                setTaskBar('第 ' + boxNo + ' 页已保存：' + (res.data.block_count || 0) + ' 个题块', 'done');
            }
            // 刷新拿的是后端裁出的真实块图与带 id 的新记录 —— 被框住的题块正是在这一步
            // 从「页图 CSS 裁剪的临时图」换成后端裁出的真实图
            refreshMistakeDetail().then(function () {
                if (state.boxSaveQueued) {
                    state.boxSaveQueued = false;
                    flushMistakeBoxSave();
                }
            });
        });
    }

    /** 「立即保存」：等不及那 1 秒，或者上一次自动保存失败想重试。 */
    function applyMistakePageBoxes() {
        const page = currentPage();
        if (!page) return;
        const blocked = boxSaveBlocker(page);
        if (blocked) { toast(blocked, 'error'); return; }
        cancelBoxSaveTimer();
        state.boxSaveQueued = false;
        flushMistakeBoxSave();
    }

    /** 操作条上的「重试」：清掉错误状态再走一次提交。 */
    function retryMistakeBoxSave() {
        state.boxSaveError = null;
        applyMistakePageBoxes();
    }

    /**
     * 这条记录是不是某个人工框生成的？是就返回那个框的序号（从 0 起）。
     *
     * 为什么必须有这一步：**框生成的记录在页图上不画块**。页图渲染对每条记录先跑
     * survivingSpans，而这类记录的矩形与框完全重合，于是被自己的框整个吃掉、返回
     * 空数组、一个像素都不画 —— 实测一份 16 页卷的第 9 页，14 条记录里 11 条是这种。
     * 只给「块」做选中高亮的方案在这页上等于什么都没做：点哪张卡片，页图上 11 个
     * 蓝虚线框看起来都一个样。所以选中态必须能落在**框**身上。
     *
     * 判定用几何而不是来源字段：records 表里没有「来自第几个框」这一列，而几何是
     * 唯一真源 —— 后端 boxes_to_blocks 就是拿框的矩形直接建块的。
     */
    /**
     * 框与记录是不是同一个矩形。
     *
     * 只此一处判定：``focusedBoxIndex``（记录 → 框）与 ``recordForBox``（框 → 记录）是一
     * 对逆运算，各写一份几何条件迟早会漂。用几何而不是来源字段，是因为后端
     * boxes_to_blocks 就是拿框的矩形直接建块 —— 「矩形相等」既精确、也不会认错。
     */
    function boxMatchesRecord(box, record) {
        if (!box || !record) return false;
        const y0 = Number(record.block_y_start);
        const y1 = Number(record.block_y_end);
        const x0 = Number(record.block_x_start == null ? 0 : record.block_x_start);
        const x1 = Number(record.block_x_end == null ? 1 : record.block_x_end);
        return Math.abs(box[1] - y0) <= BOX_FOCUS_MATCH_TOLERANCE_Y &&
            Math.abs(box[3] - y1) <= BOX_FOCUS_MATCH_TOLERANCE_Y &&
            Math.abs(box[0] - x0) <= BOX_FOCUS_MATCH_TOLERANCE_X &&
            Math.abs(box[2] - x1) <= BOX_FOCUS_MATCH_TOLERANCE_X;
    }

    function focusedBoxIndex(page, record) {
        if (!page || !record) return null;
        const boxes = boxesFor(page.page_no);
        for (let index = 0; index < boxes.length; index += 1) {
            if (boxMatchesRecord(boxes[index], record)) return index;
        }
        return null;
    }

    /**
     * 人工框对应的那条记录 —— focusedBoxIndex 的逆。
     *
     * 为什么必须有这一步：**框生成的那条记录在页图上不画块**。renderPageView 对每条
     * 记录先跑 survivingSpans，而这类记录的矩形与框完全重合，于是被自己的框整个吃掉、
     * 一个像素都不画（见 focusedBoxIndex 上方那段注释里的实测）。所以页图上唯一可触
     * 的区域就是那个框本身 —— 不给框接线，这类记录在页图上等于完全点不到。
     * 实测当前批次 107 条记录里 52 条是这种，第 9 页 11/11 全中。
     *
     * 刻意只做**精确匹配**，不做「包含框中心就算」的模糊兜底：人工合并之后的记录矩形
     * 是若干块的并集，会把好几个框一起吞进来，模糊匹配就变成「点哪个框都跳到同一条」。
     * 匹配不到就不动 —— 宁可没反应，也不要跳错地方。
     */
    function recordForBox(page, index) {
        if (!page) return null;
        const box = boxesFor(page.page_no)[index];
        if (!box) return null;
        const hits = recordsOnPage(page.page_no).filter(function (record) {
            return boxMatchesRecord(box, record);
        });
        return hits.length ? hits[0] : null;
    }

    /**
     * 页图上画一个框：蓝色虚线 + 尺寸角标 + 删除/改大小两个把手。
     *
     * ``focusLabel`` 非空＝这条框正是右侧卡片当前选中的那一块（矩形重合）：换成琥珀
     * 实线、角标改写成「当前 · 块N」。与选中的块用同一套颜色和文案 —— 它们本就是
     * 同一道题的两种画法（框生成块、块被自己的框吃掉），两种都标成「当前」才不会
     * 让人以为选中了两个不同的东西。
     *
     * 光标与 title 分两种模式：画框模式下框是要拖的（``move``），出了画框模式框是要
     * **点**的（``pointer``，点＝选中右侧对应题块，见 bindOverlayEvents）。写死成
     * ``move`` 会摆出「能拖」的样子，可非画框模式下 pointerdown 直接早退、根本拖不动。
     */
    function boxHtml(box, index, selected, focusLabel) {
        const width = Math.max(0.2, (box[2] - box[0]) * 100);
        const height = Math.max(0.2, (box[3] - box[1]) * 100);
        const focus = !!focusLabel;
        const edge = focus ? '#d97706' : (selected ? '#1d4ed8' : '#3b82f6');
        const tint = focus ? '217,119,6' : '59,130,246';
        return '<div class="absolute z-30" data-box="' + index + '" ' +
            'style="left:' + (box[0] * 100) + '%;top:' + (box[1] * 100) + '%;width:' + width + '%;height:' + height + '%;' +
            'border:2px ' + (focus ? 'solid' : 'dashed') + ' ' + edge + ';' +
            'background:rgba(' + tint + ',' + (selected ? '0.18' : (focus ? '0.14' : '0.08')) + ');' +
            'cursor:' + (state.boxMode ? 'move' : 'pointer') + ';' +
            (focus ? 'box-shadow:0 0 0 3px rgba(217,119,6,0.45);' : '') + '" ' +
            'title="' + (state.boxMode
                ? '拖动框体可整体移动；拖右下角改大小；点右上角 ✕ 删掉这个框'
                : '点击＝选中右侧对应的题块；要移动或删除这个框，先点「画框」') + '">' +
            '<span class="absolute left-0 -top-4 text-[9px] ' + (focus ? 'bg-amber-600' : 'bg-blue-600') +
            ' text-white px-1 rounded whitespace-nowrap pointer-events-none">' +
            (focus
                ? '当前 · 块' + focusLabel
                : '框 ' + (index + 1) + ' · ' + Math.round((box[2] - box[0]) * 100) + '×' + Math.round((box[3] - box[1]) * 100) + '%') +
            '</span>' +
            '<span class="absolute -right-1.5 -top-1.5 w-4 h-4 rounded-full bg-blue-600 text-white text-[9px] flex items-center justify-center" ' +
            'data-box-remove="' + index + '" title="删除这个框" style="cursor:pointer">✕</span>' +
            '<span class="absolute right-0 bottom-0 w-3.5 h-3.5" data-box-resize="' + index + '" title="拖动改大小" ' +
            'style="cursor:nwse-resize;background:linear-gradient(135deg,rgba(0,0,0,0) 45%,#2563eb 45%,#2563eb 100%)"></span>' +
            '</div>';
    }

    /** 正在拖出来的框：只勾线不填色，避免和已落定的框混淆。 */
    function boxDraftHtml(box) {
        return '<div class="absolute z-30 pointer-events-none" style="left:' + (box[0] * 100) + '%;top:' + (box[1] * 100) + '%;' +
            'width:' + Math.max(0.2, (box[2] - box[0]) * 100) + '%;height:' + Math.max(0.2, (box[3] - box[1]) * 100) + '%;' +
            'border:2px solid #1d4ed8;background:rgba(29,78,216,0.12)"></div>';
    }

    function renderBoxBar(page) {
        const bar = el('mistakeBoxBar');
        if (!bar) return;
        if (!page || !state.boxMode) {
            bar.classList.add('hidden');
            return;
        }
        bar.classList.remove('hidden');
        const boxes = boxesFor(page.page_no);
        const dirty = !!state.boxDirty[page.page_no];
        const countEl = el('mistakeBoxCount');
        if (countEl) countEl.textContent = boxes.length ? (boxes.length + ' 个框') : '0 个框';

        // 保存状态必须一直可见：自动保存是「静默」的，用户唯一能确认它发生了的地方
        // 就是这一行；失败时更要写明原因，否则他会以为框丢了、重画一遍
        const status = el('mistakeBoxStatus');
        if (status) {
            if (state.boxSaveError) {
                status.textContent = state.boxSaveError;
                status.className = 'min-w-0 text-[10px] font-semibold text-red-600 dark:text-red-400';
            } else if (state.boxSaving) {
                status.textContent = '保存中…';
                status.className = 'shrink-0 text-[10px] font-semibold text-blue-700 dark:text-blue-300';
            } else if (dirty) {
                status.textContent = '未保存 · 停手 1 秒自动保存';
                status.className = 'shrink-0 text-[10px] font-semibold text-blue-700 dark:text-blue-300';
            } else if (boxes.length) {
                status.textContent = '已保存';
                status.className = 'shrink-0 text-[10px] font-semibold text-emerald-600 dark:text-emerald-400';
            } else {
                status.textContent = '';
                status.className = '';
            }
        }
        const retry = el('mistakeBoxRetryBtn');
        if (retry) retry.classList.toggle('hidden', !state.boxSaveError);

        const hint = el('mistakeBoxHint');
        if (hint) {
            hint.textContent = boxes.length
                ? '拖动框体可移动，右下角改大小，右上角 ✕ 删除。虚线描边的题块＝被框切走一部分（框内是剩下的残段）。'
                : '在页面上按住拖出矩形（建议框住整道题，含题干与选项）。框到哪，哪才归框，没被框到的部分原样保留。';
        }
    }

    function renderPageView() {
        const page = currentPage();
        const image = el('mistakePageImage');
        const overlay = el('mistakePageOverlay');
        const pageInput = el('mistakePageInput');
        const pageTotal = el('mistakePageTotal');
        const wrap = el('mistakePageWrap');
        if (!image || !overlay) return;
        // 页号住在输入框里：写值 + 写总页数。**正在输入时不覆盖** —— 后台任何一次
        // renderPageView（识别进度、框保存回调）都会把用户正打的字抹掉。
        if (pageInput && document.activeElement !== pageInput) {
            pageInput.value = state.pages.length ? String(state.pageIndex + 1) : '';
        }
        if (pageTotal) pageTotal.textContent = '/ ' + state.pages.length;
        if (wrap) wrap.style.width = Math.round(state.zoom * 100) + '%';
        if (zoomLabel()) zoomLabel().textContent = Math.round(state.zoom * 100) + '%';
        if (!page) {
            image.removeAttribute('src');
            overlay.innerHTML = '<div class="absolute inset-0 flex items-center justify-center text-xs text-slate-400">还没有页图，先点「切题 / 重切」</div>';
            renderLayoutSwitch(null);
            renderBoxBar(null);
            return;
        }
        if (image.getAttribute('src') !== page.url) image.setAttribute('src', page.url);
        const layout = effectiveLayout(page);
        const pageRecords = recordsOnPage(page.page_no);
        const boxes = boxesFor(page.page_no);

        // 题块：双栏页上每块只覆盖自己那一栏的横向范围（不再整页宽涂色）。
        // 页上有框时改画「被框切完剩下的残段」—— 预览必须等于应用后的结果，否则
        // 用户框住半块、应用后凭空多出一个碎片卡片，就成了「怎么又切这么细」的重演。
        // 点右侧卡片跳过来的那一次渲染：只让这一块闪一下（见 selectRecord）。立刻清掉，
        // 后续因别的原因重渲染时不再闪 —— 每次重渲染都闪就成了满屏闪烁。
        const flashId = state.pageFlashId;
        state.pageFlashId = null;

        let html = pageRecords.map(function (record) {
            const grad = record.grad_status || 'unknown';
            const selected = record.id === state.selectedRecordId;
            const pickOrder = mergePickOrder(record.id);
            const tint = BLOCK_TINT[grad] || BLOCK_TINT.unknown;
            const border = BLOCK_BORDER[grad] || BLOCK_BORDER.unknown;
            const badge = grad === 'incorrect'
                ? '<span class="absolute -left-1 -top-1 w-4 h-4 rounded-full bg-red-500 text-white text-[9px] flex items-center justify-center">错</span>'
                : (grad === 'correct' ? '<span class="absolute -left-1 -top-1 w-4 h-4 rounded-full bg-emerald-500 text-white text-[9px] flex items-center justify-center">对</span>' : '');
            // 合并题按成员矩形画：并集只是拼接容器，画出来会连无关内容一起圈进去
            // （整页大的选中环就是这么来的，见 recordMemberRects）。
            const members = recordMemberRects(page, record);
            const start = Math.max(0, Math.min(1, Number(record.block_x_start == null ? 0 : record.block_x_start)));
            const end = Math.max(start + 0.002, Math.min(1, Number(record.block_x_end == null ? 1 : record.block_x_end)));
            const regions = members || [{
                x0: start,
                y0: Number(record.block_y_start),
                x1: end,
                y1: Number(record.block_y_end)
            }];
            // 合并题横跨栏时「左栏/右栏」无从说起，索性不标；单栏与普通块照旧
            const columnTag = (layout.isDouble && !members)
                ? ' · ' + columnLabel(columnOf(layout, record))
                : '';
            // 没框时给一个恒等区间，走的是与原实现完全相同的分支，零框页零变化
            const regionSpans = regions.map(function (region) {
                return boxes.length
                    ? survivingSpansIn(region.x0, region.x1, region.y0, region.y1, boxes)
                    : [[region.y0, region.y1]];
            });
            const spanCount = regionSpans.reduce(function (n, spans) { return n + spans.length; }, 0);
            // 单区域时等价于原来的 spans.length !== 1；多区域时「每个成员各留 1 段」才算没被切
            const partial = boxes.length > 0 && spanCount !== regions.length;
            const note = partial ? '（被框切走一部分，原块剩 ' + spanCount + ' 段）' : '';
            // 整块被框吃掉：该区域的 spans 为空，什么都不画 —— 它确实不该再出现在页面上
            let blockHtml = '';
            regionSpans.forEach(function (spans, regionIndex) {
                const region = regions[regionIndex];
                spans.forEach(function (span, spanIndex) {
                    // 取四位小数：0.3-0.05 这类减法在 IEEE754 下会算出 24.999999999999996%，
                    // 写进 style 能被浏览器接受，但让「渲染结果」变成一串随机尾数，没法断言
                    const top = Number((span[0] * 100).toFixed(4));
                    const height = Math.max(0.5, Number(((span[1] - span[0]) * 100).toFixed(4)));
                    blockHtml += '<div class="absolute ' + (state.boxMode ? '' : 'cursor-pointer') + '" data-record="' + record.id + '" ' +
                        'style="left:' + (region.x0 * 100) + '%;width:' + ((region.x1 - region.x0) * 100) + '%;top:' + top + '%;height:' + height + '%;background:' + tint + ';' +
                        'border-top:1px solid ' + border + ';border-bottom:1px solid ' + border + ';' +
                        // 画框模式下自动块不接事件：否则「在块上起手拖框」会被它抢走
                        (state.boxMode ? 'pointer-events:none;' : '') +
                        (partial ? 'outline:1px dashed ' + border + ';outline-offset:-2px;' : '') +
                        // 选中态刻意避开蓝色：蓝是人工框的颜色，而框与块**坐标重合**是常态
                        // （把「切得太细的碎片」框成一道题，框就盖在原先那块上），框又画在
                        // z-30 的上一层 —— 沿用蓝色内环时，点哪张卡片页图上看起来都一样。
                        // 琥珀色实线外扩 3px，再把选中块抬到 z-40 压过框，配一个页内块号角标，
                        // 才能一眼确认「现在选的是这一块」。外圈之外还留内环：块被上下两段
                        // 切开时，靠近边缘那段的描边会互相压住。
                        // 合并题**每个成员各套一圈**：只给并集画一圈时，成员离得远就会撑成
                        // 一整页，看着像全选中（2026-09-18 用户反馈）。
                        (selected ? 'box-shadow:0 0 0 3px #f59e0b, inset 0 0 0 2px #f59e0b;z-index:40;' : '') + '" ' +
                        'title="题块 ' + (record.block_index + 1) + columnTag + note + (record.question_no ? '（原卷 ' + esc(record.question_no) + '）' : '') + '">' +
                        (record.block_index === 0 || selected ? badge : '') +
                        // 角标只挂在第一个成员的第一段上：合并题每个成员都挂一个「当前」，
                        // 反而看不出它们是一道题；成员序号另有下面那个紫圈负责
                        (selected && regionIndex === 0 && spanIndex === 0
                            ? '<span class="absolute left-1/2 -translate-x-1/2 -top-4 text-[9px] font-semibold bg-amber-500 text-white px-1.5 py-0.5 rounded-full whitespace-nowrap pointer-events-none">当前 · 块' + (record.block_index + 1) + '</span>'
                            : '') +
                        // 合并题的成员序号：序号即拼接顺序，跟多选阶段那个紫圈是同一套语义
                        (selected && members
                            ? '<span class="absolute left-1 -top-1.5 w-4 h-4 rounded-full bg-violet-600 text-white text-[9px] flex items-center justify-center pointer-events-none" title="这道合并题的第 ' + (regionIndex + 1) + ' 块（共 ' + regions.length + ' 块）">' + (regionIndex + 1) + '</span>'
                            : '') +
                        // 多选中的块标出拼接序号：页图上看位置最准，序号直接告诉你
                        // 「先点的是 ①，会拼在上面（或左边）」
                        (pickOrder
                            ? '<span class="absolute -right-1 -top-1 w-4 h-4 rounded-full bg-violet-600 text-white text-[9px] flex items-center justify-center">' + pickOrder + '</span>'
                            : '') +
                        // 闪烁层单独做一个子元素：父元素身上挂着 JS 生成的内联
                        // background/box-shadow（染色、选中环），同类属性上动画打不过内联样式
                        (flashId === record.id ? '<span class="mistake-block-flash"></span>' : '') +
                    '</div>';
                });
            });
            return blockHtml;
        }).join('');

        // 分栏线：双栏时始终可见（它同时在告诉你「这页被判成两栏」），可拖动。
        // 画框模式下它只作参考、不接事件 —— 否则在分栏线附近起手拖框会被它抢走。
        if (layout.isDouble) {
            const manual = !!state.pageLayouts[page.page_no];
            html += '<div class="absolute inset-y-0 z-20" data-layout-handle="1" ' +
                'style="left:calc(' + (layout.boundary * 100) + '% - 7px);width:14px;cursor:col-resize;' +
                (state.boxMode ? 'pointer-events:none;' : '') + '" ' +
                'title="分栏线：拖动可调整左右栏分界">' +
                '<div class="absolute inset-y-0 left-1/2 -translate-x-1/2 border-l-2 ' +
                (manual ? 'border-brand-500' : 'border-blue-400/70') + ' border-dashed"></div>' +
                '</div>';
        }

        // 人工框画在最上层（z-30）：拖着框的时候它是主角，自动块的染色不该压住它
        if (boxes.length || state.boxDraft) {
            // 选中的记录若是某个人工框生成的（矩形重合），页图上该标的是**那个框** ——
            // 这类记录被自己的框整个吃掉、一个像素都不画，不标框就等于什么都没标。
            const focusedRecord = findRecord(state.selectedRecordId);
            const focusBox = focusedRecord ? focusedBoxIndex(page, focusedRecord) : null;
            const focusLabel = focusedRecord ? (focusedRecord.block_index + 1) : '';
            html += boxes.map(function (box, index) {
                return boxHtml(box, index, index === state.boxSelected,
                    index === focusBox ? focusLabel : '');
            }).join('');
            if (state.boxDraft) html += boxDraftHtml(state.boxDraft);
        }
        overlay.innerHTML = html;
        overlay.style.cursor = state.boxMode ? 'crosshair' : 'default';
        renderLayoutSwitch(page);
        renderBoxBar(page);
        bindOverlayEvents();
    }

    /** 顶部「本页栏目」三态开关 + 判定来源/置信度提示。 */
    function renderLayoutSwitch(page) {
        const box = el('mistakeLayoutSwitch');
        const hint = el('mistakeLayoutHint');
        if (!box) return;
        if (!page) {
            box.innerHTML = '';
            if (hint) hint.textContent = '';
            return;
        }
        const manual = state.pageLayouts[page.page_no];
        const current = manual ? manual.mode : 'auto';
        box.innerHTML = LAYOUT_SWITCH.map(function (item) {
            const active = current === item.key;
            return '<button type="button" title="' + esc(item.title) + '" class="px-2 py-0.5 rounded-md text-[10px] font-semibold ' +
                (active ? 'bg-brand-600 text-white' : 'text-slate-500 hover:bg-white/70 dark:hover:bg-slate-700') +
                '" onclick="setMistakeLayout(\'' + item.key + '\')">' + item.label + '</button>';
        }).join('');
        if (!hint) return;
        const layout = effectiveLayout(page);
        let text;
        if (manual) {
            text = '人工指定：' + (layout.isDouble ? '双栏' : '单栏');
        } else {
            const confidence = Number((page.layout || {}).confidence == null ? 1 : page.layout.confidence);
            const source = { text_layer: '文本层', visual: '图像', assumed: '无内容可判', manual: '人工' }[layout.source] || layout.source;
            text = '自动判定：' + (layout.isDouble ? '双栏' : '单栏') + '（' + source + ' ' + Math.round(confidence * 100) + '%）';
        }
        if (state.layoutDirty[page.page_no]) {
            hint.innerHTML = '<span class="text-brand-600 font-semibold">' + esc(text) + ' · 待重切</span>' +
                ' <button type="button" class="glass-btn glass-btn-primary px-2 py-0.5 text-[10px] font-semibold" onclick="startMistakeCut()">按新栏目重切</button>';
        } else {
            hint.textContent = text;
        }
    }

    function setMistakeLayout(key) {
        const page = currentPage();
        if (!page) return;
        const pageNo = page.page_no;
        const before = layoutSignature(page);
        if (key === 'auto') {
            delete state.pageLayouts[pageNo];
        } else if (key === 'single' || key === 'double') {
            state.pageLayouts[pageNo] = { mode: key };
        } else {
            return;
        }
        // 只有栏结构真变了才要重切（「自动」也要重切才能退回去），没变就别催用户；
        // 比较里带上分栏线：同样是双栏，线从 0.6 挪回 0.49 也得重切。
        if (layoutSignature(page) === before) {
            delete state.layoutDirty[pageNo];
        } else {
            state.layoutDirty[pageNo] = true;
            toast('栏目已改为「' + (key === 'double' ? '双栏' : (key === 'single' ? '单栏' : '自动')) + '」，点「按新栏目重切」后生效');
        }
        renderPageView();
    }

    /** 栏结构的比对签名：模式 + 分栏线（分栏线只在双栏时有意义）。 */
    function layoutSignature(page) {
        const layout = effectiveLayout(page);
        if (!layout.isDouble) return 'single';
        return 'double@' + layout.boundary.toFixed(3);
    }

    function zoomLabel() { return el('mistakeZoomLabel'); }

    function bindOverlayEvents() {
        const overlay = el('mistakePageOverlay');
        if (!overlay || overlay.dataset.bound === '1') return;
        overlay.dataset.bound = '1';
        overlay.addEventListener('click', function (event) {
            // 刚拖完分栏线 / 刚拖完框：pointerup 之后浏览器还会补一个 click，
            // 不吞掉就会在画框模式下取消刚选中的框
            if (Date.now() < state.layoutDragUntil) return;
            if (Date.now() < state.boxDragUntil) return;
            if (state.boxMode) {
                // 画框模式下自动块是 pointer-events:none，能点到的只有框本身和页空白：
                // 点框＝选中（加粗描边），点空白＝取消选中。画 / 移 / 改大小都在 pointerdown 里。
                const boxNode = event.target.closest('[data-box]');
                const picked = boxNode ? Number(boxNode.dataset.box) : null;
                if (state.boxSelected !== picked) {
                    state.boxSelected = picked;
                    renderPageView();
                }
                return;
            }
            // 非画框模式下，人工框就是它那条记录在页图上的**唯一**落点：框生成的记录
            // 被 survivingSpans 判成「整块被自己的框吃掉」，一个像素都不画。不在这里
            // 接线，这类记录在页图上就完全点不到（实测 107 条里 52 条是这种）。
            const boxHit = event.target.closest('[data-box]');
            if (boxHit) {
                // 框上那两个编辑把手（✕ 删框 / 改大小）只在画框模式里干活。非画框模式下
                // 点它们仍按原样什么都不做 —— 别把「点框＝选中题块」套上去，否则点 ✕
                // 会跳卡片，看起来像删框删出了别的东西。
                if (event.target.closest('[data-box-remove],[data-box-resize]')) return;
                const boxedRecord = recordForBox(currentPage(), Number(boxHit.dataset.box));
                if (boxedRecord) selectRecord(boxedRecord.id);
                return;
            }
            const block = event.target.closest('[data-record]');
            if (!block) return;
            const recordId = Number(block.dataset.record);
            // ⌘/Ctrl + 点击＝加入合并选择。Mac 上 ⌘+点击链接默认开新标签，这里
            // 块不是链接，但要显式拦掉冒泡，免得将来改成 <a> 时行为突变。
            if (event.metaKey || event.ctrlKey) {
                event.preventDefault();
                toggleMergePick(recordId);
                return;
            }
            selectRecord(recordId);
        });
        // 画框模式：画 / 移 / 改大小三种手势共用一套 document 级 move·up
        overlay.addEventListener('pointerdown', function (event) {
            if (!state.boxMode) return;
            const page = currentPage();
            if (!page) return;
            // 删除是「点一下」而不是拖拽，放在 pointerdown 里立即执行，用户不用等松手
            const removeNode = event.target.closest('[data-box-remove]');
            if (removeNode) {
                event.preventDefault();
                state.boxDragUntil = Date.now() + DRAG_CLICK_GUARD_MS;
                removeMistakeBox(Number(removeNode.dataset.boxRemove));
                return;
            }
            const resizeNode = event.target.closest('[data-box-resize]');
            const boxNode = event.target.closest('[data-box]');
            if (!resizeNode && !boxNode && event.button !== 0) return;
            event.preventDefault();
            const rect = overlay.getBoundingClientRect();
            const boxes = boxesFor(page.page_no);
            // 一律夹到页内：指针拖到页图外的留白上时，不夹会算出负数坐标，
            // 整个框会被后端判成越界丢掉，用户只看到「框没了」
            const point = function (ev) {
                return [
                    Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width)),
                    Math.max(0, Math.min(1, (ev.clientY - rect.top) / rect.height))
                ];
            };
            const start = point(event);
            // 吸附要按「用户眼前看到的那条分栏线」算：拖动中的线还没重切，但用户
            // 就是照着它在框题
            const layout = effectiveLayout(page);
            let changed = false;
            const move = function (moveEvent) {
                const now = point(moveEvent);
                if (resizeNode) {
                    const index = Number(resizeNode.dataset.boxResize);
                    const box = boxes[index];
                    if (!box) return;
                    boxes[index] = snapBoxToColumn(normalizeBox(box[0], box[1], now[0], now[1]), layout);
                } else if (boxNode) {
                    const index = Number(boxNode.dataset.box);
                    const box = boxes[index];
                    if (!box) return;
                    // 整体平移：先算位移再夹回页内，否则拖到页边时框会被压成一条线
                    const width = box[2] - box[0];
                    const height = box[3] - box[1];
                    const x0 = Math.max(0, Math.min(1 - width, box[0] + (now[0] - start[0])));
                    const y0 = Math.max(0, Math.min(1 - height, box[1] + (now[1] - start[1])));
                    boxes[index] = snapBoxToColumn([x0, y0, x0 + width, y0 + height], layout);
                    state.boxSelected = index;
                } else {
                    state.boxDraft = snapBoxToColumn(normalizeBox(start[0], start[1], now[0], now[1]), layout);
                }
                changed = true;
                renderPageView();
            };
            const detach = function () {
                document.removeEventListener('pointermove', move);
                document.removeEventListener('pointerup', up);
                document.removeEventListener('pointercancel', onCancel);
            };
            const up = function () {
                detach();
                if (state.boxDraft) {
                    const draft = state.boxDraft;
                    state.boxDraft = null;
                    // 画完紧接着的那个 click 会落在新框上，吞掉它 —— 否则「画」和「点选」
                    // 会挤在一次手势里，用户看到框闪一下
                    state.boxDragUntil = Date.now() + DRAG_CLICK_GUARD_MS;
                    addMistakeBox(draft);
                    return;
                }
                if (!changed) return;   // 没动过的单击：不吞 click，交给 click 处理器去选中它
                // 移动/改大小只改本地副本，落库由自动保存（停手 1 秒）或「立即保存」负责
                state.boxDragUntil = Date.now() + DRAG_CLICK_GUARD_MS;
                markPageBoxesDirty(page);
            };
            const onCancel = function () {
                // 手势被打断（触控笔离开、系统抢走指针）：草框丢掉，别在页上留半截矩形
                detach();
                state.boxDraft = null;
                state.boxDragUntil = Date.now() + DRAG_CLICK_GUARD_MS;
                renderPageView();
            };
            document.addEventListener('pointermove', move);
            document.addEventListener('pointerup', up);
            document.addEventListener('pointercancel', onCancel);
        });
        // 分栏线拖动：pointerdown 挂在 overlay 上（会随 innerHTML 重建），
        // move/up 挂 document，这样重绘时拖拽不会中断
        overlay.addEventListener('pointerdown', function (event) {
            if (state.boxMode) return;  // 画框模式下分栏线已 pointer-events:none，这里只是兜底
            const handle = event.target.closest('[data-layout-handle]');
            if (!handle) return;
            const page = currentPage();
            if (!page) return;
            event.preventDefault();
            const rect = overlay.getBoundingClientRect();
            const move = function (moveEvent) {
                const ratio = (moveEvent.clientX - rect.left) / rect.width;
                // 夹在 0.15–0.85：留出两侧的页边空白，别把某一栏拖成没有
                const boundary = Math.max(0.15, Math.min(0.85, ratio));
                state.pageLayouts[page.page_no] = { mode: 'double', boundary: boundary };
                state.layoutDirty[page.page_no] = true;
                renderPageView();
            };
            const up = function () {
                document.removeEventListener('pointermove', move);
                document.removeEventListener('pointerup', up);
                state.layoutDragUntil = Date.now() + DRAG_CLICK_GUARD_MS;
            };
            document.addEventListener('pointermove', move);
            document.addEventListener('pointerup', up);
        });
    }

    // ---------------------------------------------------------------- 页码跳转与停留位置
    //
    // 页码此前只能靠左右箭头一页页走，16 页的卷子翻到第 14 页要点 13 次。这里把页号
    // 本身做成输入框（回车或值变即跳），并把停留位置按批次记进 localStorage ——
    // 退出再点进同一批次，回到离开时那一页。

    /** 每个批次各记一份停留页序，换批次不会串。 */
    const MISTAKE_PAGE_KEY_PREFIX = 'mathbank_mistake_page_';

    /** 读回某批次上次停留的页序（0 基）。没记录、读不出、格式不对都回落到第一页。 */
    function savedPageIndexOf(batchId) {
        if (batchId === undefined || batchId === null) return 0;
        try {
            const value = Number(localStorage.getItem(MISTAKE_PAGE_KEY_PREFIX + batchId));
            return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
        } catch (error) {
            // 无痕模式等 localStorage 不可用：记不住就算了，不能连翻页本身都失败
            return 0;
        }
    }

    function savePageIndex() {
        if (!state.batch) return;
        try {
            localStorage.setItem(MISTAKE_PAGE_KEY_PREFIX + state.batch.id, String(state.pageIndex));
        } catch (error) { /* 同上：记不住不影响当前这次操作 */ }
    }

    function forgetPageIndex(batchId) {
        try { localStorage.removeItem(MISTAKE_PAGE_KEY_PREFIX + batchId); } catch (error) { /* 同上 */ }
    }

    /**
     * 切页的唯一出口。
     *
     * 原来 state.pageIndex 有三处分头赋值（进批次、箭头翻页、跨页跳转）。加「记住停留
     * 页」以后，任何一处漏改都表现成「这次翻过去了、下次进来还是老页」——只在重进时
     * 才暴露。收敛成一处，顺手把落盘、页图重绘、右栏重建一起做掉。
     */
    function setPageIndex(next) {
        if (!state.pages.length) return false;
        const target = Math.min(state.pages.length - 1, Math.max(0, Math.floor(Number(next))));
        if (!Number.isFinite(target) || target === state.pageIndex) return false;
        // 换页前把这页攒着的框立刻送出去：页一换 currentPage() 就变了，晚一步的
        // 自动保存会拿新页去提交，旧页的框就永远留在本地了
        cancelBoxSaveTimer();
        flushMistakeBoxSave();
        state.pageIndex = target;
        savePageIndex();
        renderPageView();
        // 右栏卡片是按 currentPage() 过滤出来的，不重建就会停在上一页的题块上 ——
        // 页图换了、卡片没换，用户点到的是「另一页的块」，还以为是工具乱跳。
        renderCards();
        // 整页换内容，列表从头看起：不重置的话 scrollTop 会按旧内容的位置被 clamp，
        // 落到新列表的中段。
        const list = el('mistakeCardList');
        if (list) list.scrollTop = 0;
        return true;
    }

    /** 把输入框写成当前页号（Esc 还原、提交后归位都用它，不必依赖重绘）。 */
    function syncPageInput() {
        const input = el('mistakePageInput');
        if (input) input.value = state.pages.length ? String(state.pageIndex + 1) : '';
        const total = el('mistakePageTotal');
        if (total) total.textContent = '/ ' + state.pages.length;
    }

    function mistakePageInputKey(event) {
        if (!event) return;
        if (event.key === 'Enter') {
            event.preventDefault();
            mistakePageCommit();
            if (event.target && typeof event.target.blur === 'function') event.target.blur();
        } else if (event.key === 'Escape') {
            // 与全局「Esc 取消」同义：撤销这次输入，回到当前页号
            event.preventDefault();
            syncPageInput();
            if (event.target && typeof event.target.blur === 'function') event.target.blur();
        }
    }

    /**
     * 提交页号输入。回车（keydown）与值变后失焦（change）都走这里。
     *
     * 越界与非法输入一律静默收敛，不弹提示：输 0 落到第 1 页、输 99 落到最后一页、
     * 输非数字原样退回当前页号。输入过程中的中间态（清空、只打了一半）不做任何事 ——
     * 边输边跳会让输入「12」的路上先跳第 1 页再跳第 12 页，页图白闪两次。
     */
    function mistakePageCommit() {
        const input = el('mistakePageInput');
        if (!input) return;
        const total = state.pages.length;
        if (!total) { syncPageInput(); return; }
        const raw = String(input.value === undefined || input.value === null ? '' : input.value).trim();
        const typed = Number(raw);
        const pageNo = raw && Number.isFinite(typed)
            ? Math.min(total, Math.max(1, Math.floor(typed)))
            : state.pageIndex + 1;
        input.value = String(pageNo);
        if (pageNo - 1 !== state.pageIndex) setPageIndex(pageNo - 1);
    }

    function mistakePageStep(delta) {
        if (!state.pages.length) return;
        const next = state.pageIndex + delta;
        // 到头了就当没点：交给 setPageIndex 会「停在同一页但重绘一次」，把右栏列表的
        // 滚动位置一并重置，看起来像点箭头会回到顶部。
        if (next < 0 || next >= state.pages.length) return;
        setPageIndex(next);
    }

    function mistakeZoom(delta) {
        state.zoom = Math.min(2.5, Math.max(0.5, state.zoom + delta));
        renderPageView();
    }

    function startMistakeCut() {
        runMistakeCut({});
    }

    function runMistakeCut(payload) {
        if (!state.batch) return;
        const body = payload || {};
        // 人工栏目每次都带上：重切是「整批重算、只替换被点名的页」，不带上就等于把
        // 用户之前改过的页悄悄退回自动判栏。
        const layouts = layoutPayload();
        if (Object.keys(layouts).length) body.page_layouts = layouts;
        const taskId = 'local';
        setTaskBar('正在创建切题任务…', 'running');
        api('/api/mistakes/batches/' + state.batch.id + '/cut', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (res) {
            if (!res.ok) {
                setTaskBar(res.data.message || '切题任务创建失败', 'error');
                if (res.status === 409) toast(res.data.message, 'error');
                return;
            }
            state.layoutDirty = {};
            pollTask(res.data.task_id, {
                onDone: function (task) {
                    if (task.status === 'completed') {
                        toast('切题完成：' + (task.block_count || 0) + ' 个题块');
                        refreshMistakeDetail();
                    } else if (task.status !== 'timeout') {
                        toast('切题失败，请检查提示条里的错误信息', 'error');
                        refreshMistakeDetail();
                    }
                }
            });
            void taskId;
        });
    }

    // ---------------------------------------------------------------- 卡片

    function renderCardFilters() {
        const box = el('mistakeCardFilters');
        if (!box) return;
        const counts = { all: state.records.length, incorrect: 0, correct: 0, unknown: 0, recognized: 0 };
        state.records.forEach(function (record) {
            counts[record.grad_status] = (counts[record.grad_status] || 0) + 1;
            if (record.recognize_status === 'done') counts.recognized += 1;
        });
        const filters = [
            { key: 'all', label: '全部 ' + counts.all },
            { key: 'incorrect', label: '错 ' + counts.incorrect },
            { key: 'correct', label: '对 ' + counts.correct },
            { key: 'unknown', label: '未批 ' + counts.unknown },
            { key: 'recognized', label: '已识别 ' + counts.recognized }
        ];
        box.innerHTML = filters.map(function (item) {
            const active = state.cardFilter === item.key;
            return '<button type="button" class="px-2 py-1 rounded-lg text-[10px] font-semibold border ' +
                (active ? 'bg-brand-600 text-white border-brand-600' : 'bg-white/70 dark:bg-slate-800/60 text-slate-500 border-slate-200 dark:border-slate-700') +
                '" onclick="setMistakeCardFilter(\'' + item.key + '\')">' + esc(item.label) + '</button>';
        }).join('');
    }

    function setMistakeCardFilter(key) {
        state.cardFilter = key;
        renderCardFilters();
        renderCards();
    }

    function visibleRecords() {
        const page = currentPage();
        let records = state.records;
        if (page) records = records.filter(function (record) { return record.page_no === page.page_no; });
        if (state.cardFilter === 'recognized') {
            records = records.filter(function (record) { return record.recognize_status === 'done'; });
        } else if (state.cardFilter !== 'all') {
            records = records.filter(function (record) { return record.grad_status === state.cardFilter; });
        }
        return records;
    }

    function renderCards() {
        const list = el('mistakeCardList');
        if (!list) return;
        const records = visibleRecords();
        const countEl = el('mistakeCardCount');
        const page = currentPage();
        const pending = pendingBoxCardsHtml(page);
        const pendingCount = page && state.boxDirty[page.page_no] ? boxesFor(page.page_no).length : 0;
        if (countEl) {
            const base = page
                ? ('第 ' + page.page_no + ' 页 · ' + records.length + ' 题')
                : (records.length + ' 题');
            countEl.textContent = pendingCount
                ? base + ' · 未保存 ' + pendingCount + ' 框'
                : (state.mergePick.length
                    ? base + ' · 已选 ' + state.mergePick.length + ' 块'
                    : base);
        }
        if (!records.length && !pending) {
            // 已删的条也要留着：整页被删空时，那是唯一的恢复入口
            list.innerHTML = hiddenBarHtml() +
                '<div class="text-center text-xs text-slate-400 py-12">这一页没有符合条件的题块。</div>';
            return;
        }
        list.innerHTML = hiddenBarHtml() + mergePickBarHtml() + pending + records.map(renderCard).join('');
        records.forEach(function (record) {
            const node = list.querySelector('[data-card="' + record.id + '"] .mistake-card-preview');
            renderMathIn(node);
        });
    }

    /**
     * 重建卡片列表时把滚动位置钉住。
     *
     * 列表是整块 innerHTML 重建的，而多选条（sticky top-0，排在卡片前面）只有
     * 「第一次选中才出现」—— 它一出现就占掉一行高度，下面的卡片整体下移一截，
     * 视觉上就是列表自己跳了一下。合并一道被切成 6 块的题要连点 6 次，每点一次
     * 跳一下根本没法用。所以按多选条高度的变化补偿：条涨了多少，滚动位置就跟多少。
     *
     * 顺带把「重建后自动把已选中块滚进视野」去掉了 —— 那句让 ⌘+多选时视野被反复
     * 拽回第一个选中的块（合并时最常被选中的就是它），而真正需要这个动作的
     * selectRecord 自己已经显式滚过一次。
     */
    function renderCardsKeepingScroll() {
        const list = el('mistakeCardList');
        if (!list) { renderCards(); return; }
        const topBefore = list.scrollTop || 0;
        const barBefore = list.querySelector('[data-merge-bar]');
        const heightBefore = (barBefore && barBefore.offsetHeight) || 0;
        renderCards();
        const barAfter = list.querySelector('[data-merge-bar]');
        const heightAfter = (barAfter && barAfter.offsetHeight) || 0;
        list.scrollTop = topBefore + (heightAfter - heightBefore);
    }

    /**
     * 待应用的框在右侧的预览缩略图。
     *
     * 拿**页图做 CSS 裁剪**而不是请后端裁一张：拖框是连续的，每次改动都往返一趟
     * 服务端会有肉眼可见的迟滞。真正的块图等自动保存（或「立即保存」）之后由后端裁出来替换。
     * 等比缩放塞进 86×200 —— 只按宽缩放的话，细长框会拉出几百像素高的图。
     */
    function boxPreviewThumb(page, box) {
        const pw = Number(page.width) || 0;
        const ph = Number(page.height) || 0;
        if (pw <= 0 || ph <= 0) {
            return '<div class="w-[86px] h-16 shrink-0 rounded-lg border border-dashed border-blue-300 flex items-center justify-center text-[10px] text-blue-400">页尺寸未知</div>';
        }
        const cropW = Math.max(1, (box[2] - box[0]) * pw);
        const cropH = Math.max(1, (box[3] - box[1]) * ph);
        const scale = Math.min(86 / cropW, 200 / cropH);
        const imgW = pw * scale;
        const imgH = ph * scale;
        // max-width:none 必须有：Tailwind 的 preflight 给 img 设了 max-width:100%，
        // 不覆盖的话整张页图会被压成 86px 宽，裁剪出来的位置全错
        return '<div class="shrink-0 overflow-hidden rounded-lg border border-blue-300 bg-white" ' +
            'style="width:' + Math.round(cropW * scale) + 'px;height:' + Math.round(cropH * scale) + 'px">' +
            '<img src="' + esc(page.url) + '" alt="待应用题块预览" class="select-none" ' +
            'style="max-width:none;width:' + imgW.toFixed(2) + 'px;height:' + imgH.toFixed(2) + 'px;' +
            'margin-left:' + (-box[0] * imgW).toFixed(2) + 'px;margin-top:' + (-box[1] * imgH).toFixed(2) + 'px">' +
            '</div>';
    }

    /** 本页「改过但还没保存」的框：在右侧实时出卡片，先在右栏确认框得对不对再落库。 */
    function pendingBoxCardsHtml(page) {
        if (!page || !state.boxDirty[page.page_no]) return '';
        const boxes = boxesFor(page.page_no);
        if (!boxes.length) return '';
        const layout = effectiveLayout(page);
        const cards = boxes.map(function (box, index) {
            const column = columnOfBox(layout, box);
            let columnTag = '';
            if (layout.isDouble) {
                columnTag = column
                    ? '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-blue-50 text-blue-600 border border-blue-200">' + columnLabel(column) + '</span>'
                    : '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-amber-50 text-amber-700 border border-amber-200">跨栏</span>';
            }
            return '<div class="rounded-xl p-3 border border-dashed border-blue-300 bg-blue-50/50 dark:bg-blue-900/10 space-y-2" data-box-preview="' + index + '">' +
                '<div class="flex items-start gap-3">' +
                    boxPreviewThumb(page, box) +
                    '<div class="min-w-0 flex-1">' +
                        '<div class="flex items-center gap-1.5 flex-wrap">' +
                            '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-blue-600 text-white">待保存</span>' +
                            '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-white/70 dark:bg-slate-800/60 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800">框 ' + (index + 1) + '</span>' +
                            columnTag +
                        '</div>' +
                        '<div class="mt-1.5 text-[11px] text-slate-600 dark:text-slate-300">' +
                            '第 ' + page.page_no + ' 页 · 占页高 ' + ((box[3] - box[1]) * 100).toFixed(1) + '%' +
                        '</div>' +
                        '<div class="mt-1.5">' +
                            '<button type="button" class="glass-btn px-2 py-1 text-[10px]" onclick="removeMistakeBox(' + index + ')">' +
                            '<i class="fa-solid fa-xmark text-[9px]"></i> 删掉这个框</button>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
        }).join('');
        return '<div class="space-y-2">' +
            '<div class="flex items-center justify-between px-0.5">' +
                '<span class="text-[11px] font-semibold text-blue-700 dark:text-blue-300">未保存的框（' + boxes.length + '）</span>' +
                '<span class="text-[10px] text-slate-400">停手 1 秒自动保存，之后换成正式题块</span>' +
            '</div>' +
            cards +
        '</div>' +
        '<div class="my-2 border-t border-dashed border-slate-300 dark:border-slate-600"></div>';
    }

    function renderCard(record) {
        const grad = record.grad_status || 'unknown';
        const selected = record.id === state.selectedRecordId;
        const subject = state.batch ? state.batch.subject : 'math';
        // 双栏页上标出这块属于哪一栏：卡片图是窄条，不标的话对不上原卷位置
        const cardPage = state.pages.filter(function (item) { return item.page_no === record.page_no; })[0];
        const cardLayout = cardPage ? effectiveLayout(cardPage) : { isDouble: false, boundary: 1 };
        const columnTag = cardLayout.isDouble
            ? '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-blue-50 text-blue-600 border border-blue-200">' +
                columnLabel(columnOf(cardLayout, record)) + '</span>'
            : '';
        const typeOptions = state.questionTypes.map(function (item) {
            return '<option value="' + esc(item.value) + '"' + (item.value === record.question_type ? ' selected' : '') + '>' + esc(item.label) + '</option>';
        }).join('');
        const reasonChips = state.reasons.map(function (reason) {
            const picked = (record.error_reason || '').split(',').map(function (token) { return token.trim(); }).indexOf(reason.value) >= 0;
            return '<button type="button" class="px-1.5 py-0.5 rounded-md text-[10px] border ' +
                (picked ? 'bg-amber-100 text-amber-800 border-amber-300' : 'bg-white/70 dark:bg-slate-800/60 text-slate-500 border-slate-200 dark:border-slate-700') +
                '" onclick="toggleMistakeReason(' + record.id + ',\'' + esc(reason.value) + '\')">' + esc(reason.value) + '</button>';
        }).join('');
        const recognizeBadge = record.recognize_status === 'done'
            ? '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-emerald-50 text-emerald-600 border border-emerald-200">已识别</span>'
            : (record.recognize_status === 'failed'
                ? '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-red-50 text-red-600 border border-red-200">识别失败</span>'
                : '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-slate-100 text-slate-500 border border-slate-200">未识别</span>');
        const importedBadge = record.question_id
            ? '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-brand-50 text-brand-700 border border-brand-200">已入库 #' + record.question_id + '</span>'
            : '';
        const preview = contentPreviewHtml(record.content, record.figure_images) ||
            '<span class="text-slate-400 text-[11px]">（本块未识别题面 —— 只有标为「错」的题需要识别）</span>';

        // 合并来的题：标出块数与拼接方式；跨页的额外写「跨页」，否则用户会以为
        // 另一页上还有一条属于这道题的记录
        const crossMerge = isCrossMerge(record);
        const mergedTag = record.merge_id
            ? '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-violet-50 text-violet-700 border border-violet-200">' +
                (crossMerge ? '跨页合并 ' : '已合并 ') + (record.merged_block_count || 2) + ' 块 · ' + mergeDirectionLabel(record.id) + '</span>'
            : '';
        const pickOrder = mergePickOrder(record.id);
        const ring = selected
            ? 'ring-2 ring-brand-500'
            : (pickOrder ? 'ring-2 ring-violet-500' : '');
        return '<div class="glass-card rounded-xl p-3 ' + ring + '" data-card="' + record.id + '" onclick="handleMistakeCardClick(event,' + record.id + ')">' +
            '<div class="flex items-start gap-3">' +
                '<div class="relative shrink-0">' +
                '<img src="' + esc(record.image_block) + '" alt="题块图" class="w-[86px] shrink-0 rounded-lg border border-slate-200 dark:border-slate-700 cursor-zoom-in" ' +
                    'onclick="handleMistakeThumbClick(event,' + record.id + ')">' +
                (pickOrder
                    ? '<span class="absolute -left-1.5 -top-1.5 w-4 h-4 rounded-full bg-violet-600 text-white text-[9px] flex items-center justify-center">' + pickOrder + '</span>'
                    : '') +
                '</div>' +
                '<div class="min-w-0 flex-1">' +
                    '<div class="flex items-center gap-1.5 flex-wrap">' +
                        '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-slate-100 dark:bg-slate-800 text-slate-500">第' + record.page_no + '页 块' + (record.block_index + 1) + '</span>' +
                        columnTag +
                        (record.question_no ? '<span class="text-[10px] px-1.5 py-0.5 rounded-md bg-slate-100 dark:bg-slate-800 text-slate-500">原卷 ' + esc(record.question_no) + '</span>' : '') +
                        recognizeBadge + importedBadge + mergedTag +
                        // 删除：ml-auto 把它顶到这一行最右，跟左边一排「第几块/栏号/
                        // 已识别」标签分开 —— 它是个破坏性操作，不该挤在标签中间。
                        '<button type="button" title="删除这个题块（⌘Z 可撤销）" ' +
                            'class="ml-auto shrink-0 w-6 h-6 rounded-lg flex items-center justify-center text-slate-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20" ' +
                            'onclick="hideMistakeRecord(' + record.id + ')"><i class="fa-solid fa-trash-can text-[10px]"></i></button>' +
                    '</div>' +
                    '<div class="mistake-card-preview mt-1.5 text-[12px] leading-relaxed text-slate-700 dark:text-slate-200 break-words">' + preview + '</div>' +
                    '<div class="mt-2 flex items-center gap-1.5 flex-wrap">' +
                        '<span class="text-[10px] text-slate-400">错因</span>' + reasonChips +
                    '</div>' +
                    '<div class="mt-2 flex items-center gap-1.5 flex-wrap">' +
                        // 只有「对 / 错」两个按钮：未批＝两个都没选中。点已选中的那个即
                        // 取消（回到未批），所以不需要第三个按钮 —— 而「未批」本身仍然是
                        // 默认态与筛选条件，只是不再占一个按钮位。
                        ['correct', 'incorrect'].map(function (key) {
                            const meta = GRAD_META[key];
                            const active = grad === key;
                            return '<button type="button" title="' + (active ? '再点一次取消判定（回到未批）' : '标为「' + meta.label + '」') + '" class="px-2 py-1 rounded-lg text-[11px] border font-semibold flex items-center gap-1 ' +
                                (active ? meta.cls : 'bg-white/70 dark:bg-slate-800/60 text-slate-500 border-slate-200 dark:border-slate-700') +
                                '" onclick="toggleMistakeGrad(' + record.id + ',\'' + key + '\')"><i class="fa-solid ' + meta.icon + ' text-[9px]"></i>' + meta.label + '</button>';
                        }).join('') +
                        '<label class="ml-1 flex items-center gap-1 text-[11px] text-slate-500 cursor-pointer">' +
                            '<input type="checkbox" class="rounded" ' + (record.include_in_handout ? 'checked' : '') +
                            ' onchange="setMistakeInclude(' + record.id + ', this.checked)">进错题库</label>' +
                    '</div>' +
                    '<div class="mt-2 flex items-center gap-1.5 flex-wrap">' +
                        '<select class="glass-select text-[10px] px-1.5 py-1 rounded-lg" onchange="setMistakeRecordType(' + record.id + ', this.value)">' + typeOptions + '</select>' +
                        // 编辑题面 / 重新识别 / 补图 / 单题入库都不在这张卡上。
                        // 卡片的职责是「把这道题选出来」：判对错、勾收录、标错因、
                        // 合并拆分、删除。题面与补图属于入库前的审校（审校页有正经
                        // 编辑框与 KaTeX 预览），入库后维护属于题库工作台。摆在卡片
                        // 上既挤，又让「入库」绕开审校门禁 —— 用户 2026-09-15 明确
                        // 要求全部移走。
                        (record.merge_id
                            ? '<button type="button" class="glass-btn px-2 py-1 text-[10px]" onclick="splitMistakeMerge(' + record.id + ')"><i class="fa-solid fa-scissors text-[9px]"></i> 拆回 ' + (record.merged_block_count || 2) + ' 块</button>' +
                              '<button type="button" class="glass-btn px-2 py-1 text-[10px]" onclick="flipMistakeMergeOrder(' + record.id + ')"><i class="fa-solid fa-arrow-down-up-across-line text-[9px]"></i> 调换顺序</button>' +
                              // 跨页题固定上下拼（两页的横向坐标各自独立），不给这个按钮
                              (crossMerge
                                  ? ''
                                  : '<button type="button" class="glass-btn px-2 py-1 text-[10px]" onclick="toggleMistakeMergeDirection(' + record.id + ')"><i class="fa-solid fa-left-right text-[9px]"></i> 改' + (mergeDirectionLabel(record.id) === '左右' ? '上下' : '左右') + '拼</button>')
                            : '') +
                    '</div>' +
                    '<div class="mt-1.5 flex items-center gap-2 text-[10px] text-slate-500">' +
                        '<span>解析：' + (record.answer_source === 'ai' ? 'AI 生成' + (record.answer_reviewed ? '（已核对）' : '（待核对，不会进 PDF）') : (record.answer_source === 'manual' ? '人工' : '无')) + '</span>' +
                        '<span>图形 ' + (record.figure_images || []).length + ' · 解析图 ' + (record.answer_images || []).length + '</span>' +
                        '<button type="button" class="text-brand-600 hover:underline" onclick="generateMistakeAnswer(' + record.id + ')">AI 生成解析</button>' +
                        (record.answer_source === 'ai' ? '<label class="flex items-center gap-1 cursor-pointer"><input type="checkbox" class="rounded" ' + (record.answer_reviewed ? 'checked' : '') + ' onchange="reviewMistakeAnswer(' + record.id + ', this.checked)">已人工核对</label>' : '') +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</div>';
    }

    function selectRecord(recordId) {
        state.selectedRecordId = recordId;
        // 让页图上对应的那一块闪一下：卡片列表一屏放不下十来条，只改一个静态蓝框，
        // 用户还得自己在页图上找它在哪 —— 点卡片的目的本来就是「看它在原卷的哪儿」。
        state.pageFlashId = recordId;
        renderPageView();
        renderCards();
        scrollCardIntoView(recordId);
        const record = findRecord(recordId);
        if (record) scrollPageToRecord(record);
    }

    function scrollCardIntoView(recordId) {
        const list = el('mistakeCardList');
        if (!list) return;
        const card = list.querySelector('[data-card="' + recordId + '"]');
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    /**
     * 把左侧页图滚到某个题块并居中。
     *
     * 用 getBoundingClientRect 而不是 offsetTop 链：页图外面套着 zoom 用的 wrap 和带
     * 内边距的滚动容器，offsetParent 一旦随样式变化偏移（改个 padding 就会），
     * offsetTop 会**悄悄**错位，而这种错位只有肉眼能发现。
     */
    function scrollPageToRecord(record) {
        const scroller = el('mistakePageScroll');
        const image = el('mistakePageImage');
        if (!scroller || !image) return;
        if (typeof scroller.scrollTo !== 'function') return;
        if (!image.getBoundingClientRect || !scroller.getBoundingClientRect) return;
        const pageBox = image.getBoundingClientRect();
        if (!pageBox || !pageBox.height) return;
        const scrollerBox = scroller.getBoundingClientRect();
        const y0 = Number(record.block_y_start || 0);
        const y1 = Number(record.block_y_end == null ? 1 : record.block_y_end);
        const x0 = Number(record.block_x_start || 0);
        const x1 = Number(record.block_x_end == null ? 1 : record.block_x_end);
        const blockTop = (pageBox.top - scrollerBox.top) + (scroller.scrollTop || 0) + y0 * pageBox.height;
        const blockLeft = (pageBox.left - scrollerBox.left) + (scroller.scrollLeft || 0) + x0 * pageBox.width;
        const blockHeight = Math.max(1, (y1 - y0) * pageBox.height);
        const blockWidth = Math.max(1, (x1 - x0) * pageBox.width);
        scroller.scrollTo({
            top: Math.max(0, blockTop - Math.max(0, (scroller.clientHeight - blockHeight) / 2)),
            left: Math.max(0, blockLeft - Math.max(0, (scroller.clientWidth - blockWidth) / 2)),
            behavior: 'smooth'
        });
    }

    function findRecord(recordId) {
        return state.records.filter(function (record) { return record.id === recordId; })[0];
    }

    // ---------------------------------------------------------------- 人工合并
    //
    // 一道题被分栏/排版切成多块时，选中它们合成一条记录。两块不相邻（本页第 1 块
    // 和第 7 块是常态），所以选择走「⌘/Ctrl + 点击」而不是框选 —— 点击顺序即拼接
    // 顺序，序号直接标在卡片与页图块上。

    /** 状态冲突时要逐项确认的字段；其余字段一律沿用 primary 那块。 */
    const MERGE_COMPARE_FIELDS = [
        {
            key: 'grad_status',
            label: '判定',
            render: function (record) {
                return (GRAD_META[record.grad_status || 'unknown'] || GRAD_META.unknown).label;
            }
        },
        {
            key: 'error_reason',
            label: '错因',
            render: function (record) { return record.error_reason || '（未填）'; }
        },
        {
            key: 'include_in_handout',
            label: '进错题库',
            render: function (record) { return record.include_in_handout ? '是' : '否'; }
        },
        {
            key: 'recognize_status',
            label: '识别结果',
            render: function (record) {
                if (record.recognize_status === 'done') return '已识别';
                if (record.recognize_status === 'failed') return '识别失败';
                if (record.recognize_status === 'skipped') return '未识别（未标错）';
                return '待识别';
            }
        }
    ];

    function pageByNo(pageNo) {
        return state.pages.filter(function (page) { return page.page_no === pageNo; })[0];
    }

    function rectOfRecord(record) {
        return [
            Number(record.block_x_start || 0),
            Number(record.block_y_start || 0),
            Number(record.block_x_end == null ? 1 : record.block_x_end),
            Number(record.block_y_end == null ? 1 : record.block_y_end)
        ];
    }

    /** 跨页合并组：成员带页号，所以它挂在批次顶层，不在任何一页的元数据里。 */
    function crossGroupOf(recordId) {
        const record = findRecord(recordId);
        if (!record || !record.merge_id) return null;
        const groups = state.crossPageMerges || [];
        return groups.filter(function (item) { return item.id === record.merge_id; })[0] || null;
    }

    /** 这一条是不是跨页合并来的（决定拆分 / 调序 / 删除走哪条通道）。 */
    function isCrossMerge(record) {
        return !!(record && record.merge_id && crossGroupOf(record.id));
    }

    /**
     * 合并组在当前元数据里的定义（含 id / 方向 / primary），找不到返回 null。
     *
     * 跨页组单独一路：成员带页号、落在批次顶层，``page.manual_merges`` 里永远查不到
     * 它。两种形态在这里归一成同一个结构，调用方就不必各自判一遍。
     */
    function mergeGroupOf(recordId) {
        const record = findRecord(recordId);
        if (!record || !record.merge_id) return null;
        const cross = crossGroupOf(recordId);
        if (cross) {
            return {
                record: record,
                page: pageByNo(record.page_no),
                groups: state.crossPageMerges || [],
                group: cross,
                cross: true
            };
        }
        const page = pageByNo(record.page_no);
        if (!page) return null;
        const groups = Array.isArray(page.manual_merges) ? page.manual_merges : [];
        const group = groups.filter(function (item) { return item.id === record.merge_id; })[0];
        return group ? { record: record, page: page, groups: groups, group: group, cross: false } : null;
    }

    /**
     * 这一条记录在页图上**真实占据**的矩形列表；不是合并题、或拿不到成员矩形时返回 null。
     *
     * 为什么必须按成员画：同页合并题记录的坐标是各成员块的**并集包围盒**（后端按并集写回
     * record），成员矩形另存在 `page.manual_merges[].rects`。成员离得远时并集能撑成整页 ——
     * 2026-09-18 实测 page2 的 m60735815：成员是左下 [0,0.895,0.4975,0.963] 与右上
     * [0.4975,0.069,1,0.265]，并集 x0..1 × y0.069..0.963 正好整页。于是点一下卡片，
     * 左边那圈琥珀选中环看起来像「整页被选中」；染色与点击命中区也跟着铺满无关内容。
     *
     * 跨页组不在此列：它的记录坐标本来就是自己那一半（members 各带 page_no + rect），
     * 压根没有并集可说 —— 它已经是这里要做的那种模型，同页组向它看齐而已。
     */
    function recordMemberRects(page, record) {
        if (!record || !record.merge_id) return null;
        if (crossGroupOf(record.id)) return null;
        const groups = page && Array.isArray(page.manual_merges) ? page.manual_merges : [];
        const group = groups.filter(function (item) { return item.id === record.merge_id; })[0];
        const rects = group && Array.isArray(group.rects) ? group.rects : null;
        if (!rects || !rects.length) return null;
        const cleaned = rects.map(function (rect) {
            return {
                x0: Math.max(0, Math.min(1, Number(rect[0]))),
                y0: Math.max(0, Math.min(1, Number(rect[1]))),
                x1: Math.max(0, Math.min(1, Number(rect[2]))),
                y1: Math.max(0, Math.min(1, Number(rect[3])))
            };
        }).filter(function (rect) {
            return rect.x1 - rect.x0 > 0 && rect.y1 - rect.y0 > 0;
        });
        return cleaned.length ? cleaned : null;
    }

    function mergeDirectionLabel(recordId) {
        // 跨页只有「上下」一种：两页的横向坐标各自独立，左右拼没有意义
        if (crossGroupOf(recordId)) return '上下';
        const found = mergeGroupOf(recordId);
        return found && found.group.direction === 'h' ? '左右' : '上下';
    }

    /**
     * 跨页合并的成员顺序：先按页码升序，同页内按点击顺序。
     *
     * 拼接顺序＝成员顺序，而跨页题几乎总是「题干在上、选项在下」。让用户必须按页序
     * 点两块太苛刻（点反了还得全部重来），这里替他归一，并用 toast 说明做了什么。
     */
    function crossMergeMembers(records) {
        return records
            .map(function (record, index) { return { record: record, index: index }; })
            .sort(function (a, b) {
                return a.record.page_no - b.record.page_no || a.index - b.index;
            })
            .map(function (item) { return item.record; });
    }

    function spansPages(records) {
        return new Set(records.map(function (record) { return record.page_no; })).size > 1;
    }

    /** 在多选里是第几块（0 ＝ 没被选）；序号即拼接顺序。 */
    function mergePickOrder(recordId) {
        const index = state.mergePick.indexOf(recordId);
        return index < 0 ? 0 : index + 1;
    }

    function toggleMergePick(recordId) {
        const index = state.mergePick.indexOf(recordId);
        if (index >= 0) {
            state.mergePick.splice(index, 1);
        } else {
            state.mergePick.push(recordId);
        }
        renderPageView();
        renderCardsKeepingScroll();
    }

    function clearMergePick() {
        if (!state.mergePick.length) return;
        state.mergePick = [];
        renderPageView();
        renderCardsKeepingScroll();
    }

    /**
     * 把卡片列表滚到某个已选块。
     *
     * 用 block:'center' 而不是 'nearest'：点序号的目的就是「把这块送到眼前」，
     * 而 nearest 在它还有一半露着时等于不动 —— 用户会以为按钮没反应。
     */
    function jumpToMergePick(recordId) {
        const record = findRecord(recordId);
        const page = currentPage();
        // 跨页多选时序号可能指向另一页的块：先把页翻过去。否则列表里根本没有这张
        // 卡片，点下去毫无反应 —— 用户会以为按钮坏了。
        if (record && page && record.page_no !== page.page_no) {
            const index = state.pages.findIndex(function (item) { return item.page_no === record.page_no; });
            if (index >= 0) setPageIndex(index);
        }
        const list = el('mistakeCardList');
        if (!list) return;
        const card = list.querySelector('[data-card="' + recordId + '"]');
        if (card && typeof card.scrollIntoView === 'function') {
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    /** 选中记录里状态不一致的字段 —— 只有这些需要用户拍板，一致的自动沿用。 */
    function mergeConflicts(records) {
        return MERGE_COMPARE_FIELDS.filter(function (field) {
            const first = field.render(records[0]);
            return records.some(function (record) { return field.render(record) !== first; });
        });
    }

    function requestMerge() {
        let records = state.mergePick.map(findRecord).filter(Boolean);
        if (records.length < 2) { toast('再选一块：⌘/Ctrl + 点击另一块', 'error'); return; }
        if (records.some(function (record) { return record.merge_id; })) {
            toast('这里面有已合并的题，请先拆开', 'error');
            return;
        }
        // 跨页（一道题被页边界切成两半）允许合：成员带页号落盘，记录只落在 primary
        // 那半所在页。同页的仍走原来的页级通道 —— 那条路已经被测透了，不必绕远。
        if (spansPages(records)) {
            const ordered = crossMergeMembers(records);
            if (ordered[0].id !== records[0].id) {
                toast('跨页题按页码顺序拼接：第 ' + ordered[0].page_no + ' 页在上');
            }
            records = ordered;
        }
        const conflicts = mergeConflicts(records);
        if (!conflicts.length) { submitMerge(records, 0); return; }
        state.mergePending = { records: records, primary: 0, conflicts: conflicts };
        renderMergeDialog();
    }

    function submitMerge(records, primary) {
        if (spansPages(records)) { submitCrossMerge(records, primary); return; }
        const page = pageByNo(records[0].page_no);
        if (!page) return;
        const merges = (Array.isArray(page.manual_merges) ? page.manual_merges : []).slice();
        merges.push({ rects: records.map(rectOfRecord), primary: Number(primary) || 0 });
        closeMergeDialog();
        state.mergePending = null;
        postMerges(
            page.page_no,
            merges,
            '已合并为 1 题（' + records.length + ' 块）',
            '合并 ' + records.length + ' 块'
        );
    }

    /**
     * 跨页合并：成员带页号提交，落盘在批次顶层。
     *
     * 记录只落在 primary 那半所在页，其余页上的那一半被后端「占用」—— 所以提交后由
     * 详情接口把两页一起带回来，只看当前页会以为另一半凭空消失了。
     */
    function submitCrossMerge(records, primary) {
        const merges = (state.crossPageMerges || []).slice();
        merges.push({
            members: records.map(function (record) {
                return { page_no: record.page_no, rect: rectOfRecord(record) };
            }),
            primary: Number(primary) || 0
        });
        closeMergeDialog();
        state.mergePending = null;
        postCrossMerges(
            merges,
            '已跨页合并为 1 题（' + records.length + ' 块）',
            '跨页合并 ' + records.length + ' 块'
        );
    }

    /**
     * 全量覆盖式提交跨页合并组（合并 / 拆分 / 调换顺序 / 删除都走这里）。
     *
     * 撤销记的是**上一份**列表：撤一步跨页合并会让两页都变，只回退一组元数据而不管
     * 页面，会留下半旧的界面。
     */
    function postCrossMerges(merges, message, undoLabel) {
        if (!state.batch) return Promise.resolve(false);
        const previous = (state.crossPageMerges || []).slice();
        return api('/api/mistakes/batches/' + state.batch.id + '/cross-merges', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cross_merges: merges })
        }).then(function (res) {
            if (!res.ok) { toast((res.data && res.data.message) || '跨页合并失败', 'error'); return false; }
            if (undoLabel) {
                pushUndo(undoLabel, function () {
                    return postCrossMerges(previous, '已恢复到上一步');
                });
            }
            state.mergePick = [];
            return refreshMistakeDetail().then(function () {
                renderPageView();
                renderCards();
                // 成员在当前切块里找不齐的组会被后端丢掉，必须说出来 —— 否则用户只
                // 看到「还是两块」，不知道刚才那一下到底做了什么
                if (res.data && res.data.dropped) {
                    toast('有 ' + res.data.dropped + ' 组在当前切块里找不到成员，已跳过');
                } else if (message) {
                    toast(message);
                }
                return true;
            });
        });
    }

    /**
     * 全量覆盖式提交本页的人工合并组（合并 / 拆分 / 调序 / 切方向都走这里）。
     *
     * ``undoLabel`` 给了才入撤销栈：撤销动作本身要再发一次请求，不能再把自己压进去。
     */
    function postMerges(pageNo, merges, message, undoLabel) {
        if (!state.batch) return Promise.resolve(false);
        const previous = ((pageByNo(pageNo) || {}).manual_merges || []).slice();
        return api('/api/mistakes/batches/' + state.batch.id + '/pages/' + pageNo + '/merges', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ merges: merges })
        }).then(function (res) {
            if (!res.ok) { toast((res.data && res.data.message) || '合并失败', 'error'); return false; }
            if (undoLabel) {
                pushUndo(undoLabel, function () {
                    return postMerges(pageNo, previous, '已恢复到上一步');
                });
            }
            state.mergePick = [];
            return refreshMistakeDetail().then(function () {
                renderPageView();
                renderCards();
                toast(message);
                return true;
            });
        });
    }

    /** 拆分：把这一组从元数据里摘掉，重建后各块各自成题。 */
    function splitMistakeMerge(recordId) {
        const found = mergeGroupOf(recordId);
        if (!found) { toast('这一题不是合并来的', 'error'); return; }
        const next = found.groups.filter(function (item) { return item.id !== found.record.merge_id; });
        const label = '拆分回 ' + (found.record.merged_block_count || 2) + ' 块';
        if (found.cross) { postCrossMerges(next, '已' + label, label); return; }
        postMerges(found.page.page_no, next, '已' + label, label);
    }

    /** 调换拼接顺序：rects 反转，primary 跟着镜像（原来以第 1 块为准就变第 2 块）。 */
    function flipMistakeMergeOrder(recordId) {
        const found = mergeGroupOf(recordId);
        if (!found) { toast('这一题不是合并来的', 'error'); return; }
        if (found.cross) {
            // 跨页组反转＝题干与选项换个上下（primary 跟着镜像，状态仍跟对那半）
            const members = (found.group.members || []).slice().reverse();
            const primary = Math.max(0, members.length - 1 - (Number(found.group.primary) || 0));
            const next = found.groups.map(function (item) {
                return item.id === found.group.id
                    ? { id: item.id, members: members, direction: 'v', primary: primary }
                    : item;
            });
            postCrossMerges(next, '已调换拼接顺序', '调换拼接顺序');
            return;
        }
        const rects = (found.group.rects || []).slice().reverse();
        const primary = Math.max(0, rects.length - 1 - (Number(found.group.primary) || 0));
        const next = found.groups.map(function (item) {
            return item.id === found.group.id
                ? { id: item.id, rects: rects, direction: item.direction, primary: primary }
                : item;
        });
        postMerges(found.page.page_no, next, '已调换拼接顺序', '调换拼接顺序');
    }

    /** 切换拼接方向（上下 ↔ 左右）。 */
    function toggleMistakeMergeDirection(recordId) {
        const found = mergeGroupOf(recordId);
        if (!found) { toast('这一题不是合并来的', 'error'); return; }
        // 卡片上不给跨页题画这个按钮，这里再兜一次（快捷键/旧 HTML 都可能绕过来）
        if (found.cross) { toast('跨页题只能上下拼接', 'error'); return; }
        const direction = found.group.direction === 'h' ? 'v' : 'h';
        const next = found.groups.map(function (item) {
            return item.id === found.group.id
                ? { id: item.id, rects: item.rects, direction: direction, primary: item.primary }
                : item;
        });
        postMerges(
            found.page.page_no,
            next,
            direction === 'h' ? '改为左右拼接' : '改为上下拼接',
            '改拼接方向'
        );
    }

    // ---------------------------------------------------------------- 删除题块
    //
    // 「删除」＝把这块从本页的渲染项里剔掉，连数据库记录一起不建（所以它不会进错题
    // 库、不进统计）。**不能只删记录**：画框、合并、重切都是「整页删旧插新」重建，
    // 重建一次它就回来了。因此名单必须落盘到 analysis.json 的 hidden_blocks，按几何
    // 矩形锚定 —— 与人工合并组同一个理由：重切后块序号会变，几何不会。

    function hiddenRectsOf(page) {
        if (!page || !Array.isArray(page.hidden_blocks)) return [];
        return page.hidden_blocks.map(function (rect) {
            return rect.map(Number);
        });
    }

    /** 本页被删掉的题块：一行小条 + 恢复入口。没有它就无从知道自己删过什么。 */
    function hiddenBarHtml() {
        const page = currentPage();
        const count = hiddenRectsOf(page).length;
        if (!count) return '';
        return '<div class="mb-2 flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-xl border border-dashed border-slate-300 dark:border-slate-600 bg-slate-50/70 dark:bg-slate-800/30">' +
            '<span class="min-w-0 text-[11px] text-slate-500"><i class="fa-solid fa-trash-can text-[9px] mr-1"></i>本页已删除 ' + count + ' 个题块</span>' +
            '<button type="button" class="shrink-0 glass-btn px-2 py-1 text-[10px]" onclick="restoreHiddenBlocks()"><i class="fa-solid fa-rotate-left text-[9px]"></i> 全部恢复</button>' +
            '</div>';
    }

    /**
     * 全量覆盖式提交本页的删除名单。
     *
     * ``undoLabel`` 给了才入撤销栈（撤销本身要再发一次请求，不能再把自己压进去，
     * 否则 ⌘Z 会自己套娃）。
     */
    function postHidden(pageNo, rects, message, undoLabel) {
        if (!state.batch) return Promise.resolve(false);
        const previous = hiddenRectsOf(pageByNo(pageNo));
        return api('/api/mistakes/batches/' + state.batch.id + '/pages/' + pageNo + '/hidden', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rects: rects })
        }).then(function (res) {
            if (!res.ok) { toast((res.data && res.data.message) || '删除失败', 'error'); return false; }
            if (undoLabel) {
                pushUndo(undoLabel, function () { return postHidden(pageNo, previous, '已恢复'); });
            }
            return refreshMistakeDetail().then(function () {
                renderPageView();
                renderCards();
                // 空消息不弹：跨页删除要连发两页的隐藏请求，中间那些不该各弹一次
                if (message) toast(message);
                return true;
            });
        });
    }

    /** 删除一张卡片对应的题块（合并来的整组一起删，不用逐块点）。 */
    function hideMistakeRecord(recordId) {
        const record = findRecord(recordId);
        if (!record) return;
        const cross = crossGroupOf(recordId);
        if (cross) { hideCrossMerge(record, cross); return; }
        const page = pageByNo(record.page_no);
        if (!page) { toast('找不到这一页', 'error'); return; }
        const label = record.merge_id
            ? '合并题'
            : '第' + record.page_no + '页块' + (record.block_index + 1);
        state.mergePick = state.mergePick.filter(function (id) { return id !== recordId; });
        if (state.selectedRecordId === recordId) state.selectedRecordId = null;
        postHidden(
            page.page_no,
            hiddenRectsOf(page).concat([rectOfRecord(record)]),
            '已删除' + label + '（⌘Z 可撤销）',
            '删除' + label
        );
    }

    /**
     * 删除一条跨页合并题：先把这个组拆掉，再把各成员矩形加进各自页的隐藏名单。
     *
     * 不能只在本页隐藏 —— 那道题的另一半在别的页上，只删一半会留下一个孤零零的
     * 「选项块」，看着像工具删漏了。撤销也一样：两页的名单一起回退，再把组合回来。
     */
    function hideCrossMerge(record, group) {
        const label = '跨页合并题';
        const previous = (state.crossPageMerges || []).slice();
        const next = previous.filter(function (item) { return item.id !== group.id; });
        const byPage = {};
        (group.members || []).forEach(function (member) {
            (byPage[member.page_no] = byPage[member.page_no] || []).push(member.rect);
        });
        const pages = Object.keys(byPage).map(Number).sort(function (a, b) { return a - b; });
        const beforeHidden = {};
        pages.forEach(function (pageNo) {
            const page = pageByNo(pageNo);
            beforeHidden[pageNo] = page ? hiddenRectsOf(page).slice() : [];
        });
        const writeHidden = function (rectsOf) {
            return pages.reduce(function (chain, pageNo) {
                return chain.then(function () { return postHidden(pageNo, rectsOf(pageNo), ''); });
            }, Promise.resolve());
        };
        state.mergePick = state.mergePick.filter(function (id) { return id !== record.id; });
        if (state.selectedRecordId === record.id) state.selectedRecordId = null;
        pushUndo('删除' + label, function () {
            return writeHidden(function (pageNo) { return beforeHidden[pageNo]; })
                .then(function () { return postCrossMerges(previous, '已恢复跨页合并题'); });
        });
        postCrossMerges(next, '').then(function (ok) {
            if (!ok) return null;
            return writeHidden(function (pageNo) {
                const page = pageByNo(pageNo);
                return (page ? hiddenRectsOf(page) : []).concat(byPage[pageNo]);
            }).then(function () {
                toast('已删除' + label + '（⌘Z 可撤销）');
                return true;
            });
        });
    }

    /** 恢复本页全部被删的题块。 */
    function restoreHiddenBlocks() {
        const page = currentPage();
        if (!page) return;
        const count = hiddenRectsOf(page).length;
        if (!count) return;
        postHidden(page.page_no, [], '已恢复 ' + count + ' 个题块', '恢复 ' + count + ' 个题块');
    }

    // ---------------------------------------------------------------- 撤销

    /** 撤销栈只记「怎么回到上一步」，不记业务语义，每一种操作自己给出回退动作。 */
    function pushUndo(label, apply) {
        state.undoStack.push({ label: label, apply: apply });
        if (state.undoStack.length > UNDO_LIMIT) state.undoStack.shift();
    }

    function undoLastAction() {
        const entry = state.undoStack.pop();
        if (!entry) { toast('没有可撤销的操作', 'error'); return; }
        entry.apply().then(function (ok) {
            // 失败的撤销要把机会还回去：否则一次网络抖动就永久吃掉这一步。
            if (ok) toast('已撤销：' + entry.label);
            else pushUndo(entry.label, entry.apply);
        });
    }

    function renderMergeDialog() {        const pending = state.mergePending;
        const dialog = el('mistakeMergeDialog');
        if (!dialog || !pending) return;
        const records = pending.records;
        const conflicts = pending.conflicts || [];
        const columns = records.map(function (record, index) {
            const active = index === pending.primary;
            const rows = conflicts.map(function (field) {
                return '<div class="flex items-baseline justify-between gap-2 py-0.5">' +
                    '<span class="text-[10px] text-slate-400">' + field.label + '</span>' +
                    '<span class="text-[11px] ' + (active ? 'font-semibold text-slate-800' : 'text-slate-600') + '">' + esc(field.render(record)) + '</span>' +
                    '</div>';
            }).join('');
            return '<div class="rounded-xl border p-2 cursor-pointer ' +
                (active ? 'border-brand-500 bg-brand-50' : 'border-slate-200 dark:border-slate-700 bg-white/70 dark:bg-slate-800/60') +
                '" onclick="pickMergePrimary(' + index + ')">' +
                '<div class="flex items-center gap-1.5">' +
                    '<span class="w-4 h-4 rounded-full text-[9px] flex items-center justify-center ' +
                        (active ? 'bg-brand-600 text-white' : 'bg-slate-200 text-slate-600') + '">' + (index + 1) + '</span>' +
                    '<span class="text-[10px] text-slate-500">第' + record.page_no + '页 块' + (record.block_index + 1) + '</span>' +
                '</div>' +
                '<img src="' + esc(record.image_block) + '" alt="块' + (index + 1) + '" class="mt-1.5 w-full rounded-lg border border-slate-200 dark:border-slate-700">' +
                '<div class="mt-1.5 space-y-0.5">' + rows + '</div>' +
            '</div>';
        }).join('');
        dialog.innerHTML = '<div class="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-4" onclick="closeMistakeMergeDialog()">' +
            '<div class="w-full max-w-2xl rounded-2xl bg-white dark:bg-slate-900 shadow-xl p-4 space-y-3" onclick="event.stopPropagation()">' +
                '<div class="text-sm font-semibold text-slate-800 dark:text-slate-100">合并这 ' + records.length + ' 块为 1 题</div>' +
                '<div class="text-[11px] text-slate-500">下面几项两块不一致，点选以哪一块为准；没列出的项目前两块一致，自动沿用。</div>' +
                '<div class="grid gap-2" style="grid-template-columns:repeat(' + records.length + ',minmax(0,1fr))">' + columns + '</div>' +
                '<div class="flex items-center justify-end gap-2 pt-1">' +
                    '<button type="button" class="glass-btn px-3 py-1.5 text-[11px]" onclick="closeMistakeMergeDialog()">取消</button>' +
                    '<button type="button" class="px-3 py-1.5 text-[11px] rounded-lg bg-brand-600 text-white font-semibold" onclick="confirmMistakeMerge()">合并（以第 ' + (pending.primary + 1) + ' 块为准）</button>' +
                '</div>' +
            '</div>' +
        '</div>';
        dialog.classList.remove('hidden');
    }

    function closeMergeDialog() {
        const dialog = el('mistakeMergeDialog');
        if (!dialog) return;
        dialog.classList.add('hidden');
        dialog.innerHTML = '';
    }

    /** 卡片列表顶部的多选操作条：序号即拼接顺序，回车直接合并。 */
    function mergePickBarHtml() {
        if (!state.mergePick.length) return '';
        // 序号本身就是「这块在哪」的线索，点一下直接滚过去 —— 一屏放不下几张卡时，
        // 靠滚轮找第 5 块很费劲，而序号一直黏在顶上、就在眼前。
        // 跨页多选时块号会在两页间重复（页 9 块1 / 页 10 块1），不带页号分不清
        const spanPages = new Set(state.mergePick.map(function (id) {
            const record = findRecord(id);
            return record ? record.page_no : 0;
        })).size > 1;
        const chips = state.mergePick.map(function (id) {
            const record = findRecord(id);
            const label = record
                ? ((spanPages ? record.page_no + '页 ' : '') + '块' + (record.block_index + 1))
                : '？';
            return '<button type="button" data-merge-jump="' + id + '"' +
                ' class="rounded px-0.5 font-medium text-violet-700 hover:bg-violet-200 hover:text-violet-900"' +
                ' title="滚到这一块"' +
                ' onclick="jumpToMistakeMergePick(' + id + ')">' + esc(label) + '</button>';
        }).join('<span class="text-violet-400"> → </span>');
        return '<div data-merge-bar class="sticky top-0 z-10 mb-2 flex items-center justify-between gap-2 rounded-xl border border-violet-300 bg-violet-50 px-3 py-2">' +
            '<div class="text-[11px] text-violet-800">已选 ' + state.mergePick.length + ' 块' +
                (spanPages ? '<span class="ml-1 rounded bg-violet-200 px-1 text-[10px]">跨页</span>' : '') +
                '：' + chips + '</div>' +
            '<div class="flex items-center gap-1.5 shrink-0">' +
                (state.mergePick.length >= 2
                    ? '<button type="button" class="px-2 py-1 text-[10px] rounded-lg bg-brand-600 text-white font-semibold" onclick="requestMistakeMerge()">⏎ 合并为一题</button>'
                    : '<span class="text-[10px] text-violet-500">再选一块</span>') +
                '<button type="button" class="glass-btn px-2 py-1 text-[10px]" onclick="clearMistakeMergePick()">Esc 取消</button>' +
            '</div>' +
        '</div>';
    }

    function patchRecord(recordId, body, options) {
        const opts = options || {};
        return api('/api/mistakes/records/' + recordId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (res) {
            if (!res.ok) {
                toast(res.data.message || '更新失败', 'error');
                return null;
            }
            const updated = res.data.record;
            const index = state.records.findIndex(function (record) { return record.id === recordId; });
            if (index >= 0) state.records[index] = updated;
            if (res.data.stats) {
                state.batch = Object.assign({}, state.batch, res.data.stats);
            }
            if (!opts.silent) {
                renderCards();
                renderPageView();
                renderStats();
                renderCardFilters();
            }
            return updated;
        });
    }

    function setMistakeGrad(recordId, value) {
        patchRecord(recordId, { grad_status: value }).then(function () { updateRecognizeButton(); });
    }

    /**
     * 卡片上的「对 / 错」按钮：点已选中的那个＝取消判定，回到未批。
     *
     * 因此界面上只需要两个按钮 —— 未批仍然存在（默认态、筛选条件、快捷键 3），
     * 只是不再占一个按钮位。快捷键 1/2 刻意不走这个开关：连按两次 1 把上一题的
     * 判定清掉，是件很难察觉的坏事。
     */
    function toggleMistakeGrad(recordId, value) {
        const record = findRecord(recordId);
        if (!record) return;
        const current = record.grad_status || 'unknown';
        setMistakeGrad(recordId, current === value ? 'unknown' : value);
    }

    function setMistakeInclude(recordId, checked) {
        patchRecord(recordId, { include_in_handout: !!checked }, { silent: true }).then(function () {
            renderStats();
        });
    }

    function setMistakeRecordType(recordId, value) {
        patchRecord(recordId, { question_type: value }, { silent: true });
    }

    function toggleMistakeReason(recordId, value) {
        const record = findRecord(recordId);
        if (!record) return;
        const current = (record.error_reason || '').split(',').map(function (token) { return token.trim(); })
            .filter(function (token) { return token.length > 0; });
        const index = current.indexOf(value);
        if (index >= 0) current.splice(index, 1); else current.push(value);
        patchRecord(recordId, { error_reason: current }, { silent: true }).then(function () { renderCards(); });
    }

    function editMistakeContent(recordId) {
        const record = findRecord(recordId);
        if (!record) return;
        const next = window.prompt('编辑题面（LaTeX + Markdown）：', record.content || '');
        if (next === null) return;
        patchRecord(recordId, { content: next }).then(function () { toast('题面已更新'); });
    }

    function reviewMistakeAnswer(recordId, checked) {
        patchRecord(recordId, { answer_reviewed: !!checked }, { silent: true });
    }

    function generateMistakeAnswer(recordId) {
        const record = findRecord(recordId);
        if (!record) return;
        if (!record.content) { toast('请先识别或填写题面，再生成解析', 'error'); return; }
        toast('正在生成 AI 解析，请稍候…', 'info');
        const form = new FormData();
        form.append('content', record.content);
        form.append('question_type', record.question_type || 'detailed_answer');
        form.append('stream', 'false');
        api('/api/ai/solve', { method: 'POST', body: form }).then(function (res) {
            if (!res.ok || !res.data.solution) {
                toast(res.data.message || 'AI 解析生成失败', 'error');
                return;
            }
            patchRecord(recordId, {
                answer_markdown: res.data.solution,
                answer_source: 'ai',
                answer_reviewed: false
            }).then(function () {
                toast('AI 解析已生成：请核对后勾选「已人工核对」，否则不会进错题库');
            });
        });
    }

    // ---------------------------------------------------------------- 识别

    function currentEngine() {
        const select = el('mistakeEngineInput');
        return select ? select.value : '';
    }

    function updateRecognizeButton() {
        const button = el('mistakeRecognizeBtn');
        if (!button) return;
        const count = state.records.filter(function (record) {
            return record.grad_status === 'incorrect';
        }).length;
        button.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>' +
            '<span>识别已标记的 ' + count + ' 道错题</span>';
        button.disabled = count === 0;
        button.classList.toggle('opacity-50', count === 0);
    }

    function startMistakeRecognize() {
        if (!state.batch) return;
        const targets = state.records.filter(function (record) {
            return record.grad_status === 'incorrect';
        }).map(function (record) { return record.id; });
        if (!targets.length) { toast('还没有标记为「错」的题', 'error'); return; }
        recognizeMistakeRecords(targets);
    }

    function recognizeOneMistake(recordId) {
        recognizeMistakeRecords([recordId]);
    }

    function recognizeMistakeRecords(recordIds, onDone, onProgress) {
        if (!state.batch) return;
        setTaskBar('正在创建识别任务…', 'running');
        api('/api/mistakes/batches/' + state.batch.id + '/recognize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ record_ids: recordIds, engine: currentEngine() })
        }).then(function (res) {
            if (!res.ok) {
                setTaskBar(res.data.message || '识别任务创建失败', 'error');
                return;
            }
            // 任务还没跑起来就先点亮队列：等第一次轮询返回有一秒空窗，
            // 那一秒里界面上「哪几道在跑」是全空的。
            state.recognizeLive = {
                active: true,
                queue: (recordIds || []).map(Number),
                done: [],
                current: null,
                errors: []
            };
            pollTask(res.data.task_id, {
                onProgress: function (task) {
                    applyRecognizeProgress(task);
                    if (typeof onProgress === 'function') onProgress(task);
                },
                onDone: function (task) {
                    // 先解锁再回调：调用方要立刻按「已跑完」的状态重绘，晚一步就会
                    // 画出「识别中」的角标和只读的编辑框。
                    state.recognizeLive.active = false;
                    state.recognizeLive.current = null;
                    // 失败原因留在状态里：只走 setTaskBar 的话，人在审校页看不到
                    // 那根任务条（它在批次详情页头部），屏幕上就只剩一句
                    // 「成功 0 道，失败 2 道」——等于没给原因。
                    state.recognizeLive.errors = Array.isArray(task.errors)
                        ? task.errors.slice() : [];
                    if (task.status === 'completed') {
                        toast('识别完成：成功 ' + (task.recognized || 0) + ' 道，失败 ' + (task.failed || 0) + ' 道');
                        if (task.failed) {
                            setTaskBar('部分题块识别失败，可在审校页点「重新识别这道」重试。失败详情：' +
                                ((task.errors || []).join('；') || '见服务端日志'), 'error');
                        }
                    } else if (task.status !== 'timeout') {
                        toast('识别失败，请查看提示条', 'error');
                    }
                    refreshMistakeDetail().then(updateRecognizeButton);
                    if (typeof onDone === 'function') onDone(task);
                }
            });
        });
    }

    // ---------------------------------------------------------------- 入库

    /**
     * 把刚入库的题灌进组卷试题篮并跳到组卷工作台。
     *
     * 三科现在都入库，「入库 → 组卷」就是错题出卷的主链路：识别定稿后一次入库，
     * 这批题随即成为一份卷子的题源。已在本篮里的不重复加。
     */
    function sendImportedToPaper(items) {
        if (!Array.isArray(items) || !items.length) return 0;
        if (typeof window.addManyToCart !== 'function') return 0;
        const pairs = items.map(function (item) {
            const type = item && item.question_type;
            return {
                id: parseInt(item && item.question_id, 10),
                // 错题以解答题为主，给个像样的默认分；选择题仍按小题分值走。
                score: (!type || type === 'detailed_answer') ? 12 : 5
            };
        }).filter(function (pair) { return pair.id > 0; });
        if (!pairs.length) return 0;
        const added = window.addManyToCart(pairs);
        if (added && typeof window.selectWorkspace === 'function') {
            window.selectWorkspace('paper', '组卷工作台');
        }
        return added;
    }

    function importMistakeBatch(recordIds) {
        if (!state.batch) return;
        const explicitIds = Array.isArray(recordIds)
            ? recordIds.map(function (value) { return parseInt(value, 10); })
                .filter(function (value) { return value > 0; })
            : [];
        // 审校页是入库前唯一的人工关口。不显式指定要入库的题就直接拒绝，
        // 不回退到后端「本批次全部已识别、尚未入库」的默认口径 —— 那等于
        // 绕过审校静默多入库，而且全程 200 OK、零提示。
        if (!explicitIds.length) {
            toast('没有指定要入库的题目。请到「错题本审校」逐题核对后再入库。', 'error');
            return;
        }
        const button = el('mrGenerateBtn');
        if (button) { button.disabled = true; }
        toast('正在入库…', 'info');
        api('/api/mistakes/batches/' + state.batch.id + '/import-to-bank', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // 后端的默认口径是「本批次全部已识别、尚未入库的题」，并不等于「本次
            // 勾选进错题本的题」。审校页只该把自己列出的那几道送进去，因此显式带
            // record_ids；不带就等于绕过审校、把没勾的题一起入库。
            body: JSON.stringify({ record_ids: explicitIds })
        }).then(function (res) {
            if (button) { button.disabled = false; }
            if (!res.ok) { toast(res.data.message || '入库失败', 'error'); return; }
            const data = res.data;
            const duplicates = data.duplicates || [];
            if (!duplicates.length) {
                toast('入库完成：新增 ' + data.imported + ' 道' +
                    (data.already ? '，已存在 ' + data.already + ' 道' : '') +
                    ((data.skipped || []).length ? '，跳过 ' + data.skipped.length + ' 道' : ''));
            } else {
                // 查重撞车不静默丢弃：逐题问「跳过 / 仍入库」
                askDuplicatesSequentially(duplicates, 0, function () {
                    toast('入库完成：新增 ' + data.imported + ' 道，撞车处理完毕');
                });
            }
            // 入库即送组卷：这批错题直接成为一份卷子的题源。
            const sentCount = sendImportedToPaper(data.imported_items || []);
            if (sentCount) {
                toast('已把 ' + sentCount + ' 道入库题放进组卷试题篮');
            }
            refreshMistakeDetail();
        });
    }

    function askDuplicatesSequentially(duplicates, index, done) {
        if (index >= duplicates.length) { if (done) done(); return; }
        const item = duplicates[index];
        const message = '第 ' + (index + 1) + '/' + duplicates.length + ' 道与原卷 ' + (item.question_no || '（无题号）') +
            ' 题撞车：\n\n库里已有（#' + item.existing_question_id + '，相似度 ' +
            Math.round((item.similarity || 0) * 100) + '%）：\n' +
            (item.existing_preview || '').slice(0, 120) + '\n\n' +
            '点「确定」= 仍要入库；点「取消」= 跳过这道题。';
        if (window.confirm(message)) {
            api('/api/mistakes/records/' + item.record_id + '/import-to-bank', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ force: true })
            }).then(function () { askDuplicatesSequentially(duplicates, index + 1, done); });
        } else {
            askDuplicatesSequentially(duplicates, index + 1, done);
        }
    }

    // ---------------------------------------------------------------- 错题本审校
    //
    // 入库前的最后一道人工关口。左边＝本次勾选进错题本的题，中间＝识别出来的
    // LaTeX（可直接编辑），右边＝渲染结果，老师一眼就能判断识别对不对。
    //
    // 这一页同时承担门禁：一期导出与入库都不看识别状态，识别没跑过时 content 是空的，
    // 会静默产出「本题面尚未识别」的占位题面。这里把「题面为空」升级成硬拦截。
    //
    // 编辑采用防抖静默保存（700ms）：这一页的编辑对象是识别输出，逐题点保存会
    // 让「检查 20 道题」变成 40 次点击；右上角常驻保存态提示，改完即落库。

    // syncedContent / syncedAnswer 记住「上次同步给用户看的服务端版本」。
    // 不能拿 textarea 直接和服务端比：识别刚落库时两者必然不同，那会被误判成
    // 「用户在打字」，于是识别结果永远回填不上。
    let reviewDraft = {
        recordId: null, timer: null, syncedContent: '', syncedAnswer: '', cursor: null,
        // syncedMeta：上一次提交成功的分类信息快照（JSON 串）。分类字段有十来个，
        // 逐个比会写成一串条件；整串比一眼就能看出「用户到底改没改」。
        syncedMeta: '',
        // 右栏解析区的显隐，对应题库「题目预览」里那个「隐藏解析」开关
        answerHidden: false
    };
    let reviewCursorBound = false;
    // 分类信息用的教材树：按批次学科拉一次就缓存，切题不再重复请求。
    let reviewMetaTree = null;
    let reviewMetaTreeSubject = '';
    // 知识点 / 解题方法（多标签）与关联章节的本地草稿，随防抖保存一起提交
    let reviewMetaTags = { knowledge: [], solve: [] };
    let reviewRelChapters = [];

    /**
     * 记住两个编辑框最后的光标位。
     *
     * 补图要插在「用户光标停的位置」，而点按钮那一刻 textarea 已经失焦、读不到
     * ``document.activeElement``。**也不能直接读 selectionStart**：用户从没把光标放进
     * 输入框时它默认是 0，插图会莫名其妙跑到题面最前面。所以只在真的 focus / 点过 /
     * 打过字之后才认这个位置，其余情况退化为「插到末尾」。
     */
    function bindReviewCursorTracking() {
        if (reviewCursorBound) return;
        reviewCursorBound = true;
        ['mrContent', 'mrAnswer'].forEach(function (id) {
            const area = el(id);
            if (!area || typeof area.addEventListener !== 'function') return;
            const remember = function () {
                if (typeof area.selectionStart === 'number') {
                    reviewDraft.cursor = { areaId: id, pos: area.selectionStart };
                }
                // 顺带重算「补这张图」浮层：它只跟光标位置有关，不需要再挂别的触发源。
                mrSyncPlaceholderAction(id);
            };
            ['focus', 'click', 'keyup', 'select', 'input'].forEach(function (type) {
                area.addEventListener(type, remember);
            });
            // 滚动会让贴着某一行的浮层飘走，重算一次（textarea 内容不长，几乎不触发）。
            area.addEventListener('scroll', remember);
            // blur 后**等一拍**再收：Safari 不认 mousedown 的 preventDefault，点浮层按钮的
            // 瞬间 textarea 仍会失焦，立刻收起等于把按钮从指针底下抽走、click 永不触发。
            area.addEventListener('blur', function () {
                setTimeout(function () { mrHidePlaceholderAction(id); }, 250);
            });
        });
    }

    /**
     * 审校页里指代一道题的说法：「第 2 页 块 5 · 原卷 12」。
     *
     * 原来用 ``question_no || page_no + ' 页'``：题号还没识别出来时，两道题会双双
     * 显示成「第 1 页」，用户根本分不清点的是哪一道；左列表那种「1. 1页」（序号
     * 拼页号）也和题卡上的「第1页 块4」对不上。统一到题卡的口径。
     */
    function reviewRecordLabel(record) {
        const label = '第 ' + (record.page_no || '?') + ' 页 块 ' + ((record.block_index || 0) + 1);
        return record.question_no ? label + ' · 原卷 ' + record.question_no : label;
    }

    /** 本次收录的题：include_in_handout 为真。 */
    function reviewRecords() {
        return (state.records || []).filter(function (record) { return !!record.include_in_handout; });
    }

    function reviewRecordStatus(record) {
        const content = String(record.content || '').trim();
        if (!content) {
            return String(record.recognize_status || 'pending') === 'failed'
                ? { label: '识别失败', cls: 'bg-rose-50 text-rose-600 border-rose-200/70' }
                : { label: '未识别', cls: 'bg-rose-50 text-rose-600 border-rose-200/70' };
        }
        if (record.question_id) {
            return { label: '已入库', cls: 'bg-sky-50 text-sky-600 border-sky-200/70' };
        }
        if (String(record.recognize_status || '') === 'failed') {
            return { label: '识别异常', cls: 'bg-amber-50 text-amber-600 border-amber-200/70' };
        }
        return { label: '已就绪', cls: 'bg-emerald-50 text-emerald-600 border-emerald-200/70' };
    }

    function reviewMissingRecords() {
        return reviewRecords().filter(function (record) {
            return String(record.recognize_status || 'pending') !== 'done' || !String(record.content || '').trim();
        });
    }

    /**
     * 进审校页时自动补识别 —— 识别是工具该干的活。
     *
     * 用户点「审校并入库」进来，就该看到 LaTeX 一道道出来，而不是自己再去找按钮。
     * 口径按 2026-09-15 的决策：
     *   - 只跑「题面还是空的、且从来没跑过」的题；
     *   - 上次识别失败（failed）的**不自动重跑** —— 那多半是引擎或配置问题，
     *     自动重跑只会反复烧额度，交给「重新识别这道」手动决定。
     * 已经在跑的话直接返回，否则会并起第二个任务（后端队列会打架）。
     */
    function autoRecognizeOnReviewOpen() {
        if (state.recognizeLive.active) return null;
        const ids = reviewRecords().filter(function (record) {
            if (String(record.content || '').trim()) return false;
            return String(record.recognize_status || 'pending') !== 'failed';
        }).map(function (record) { return record.id; });
        if (!ids.length) return null;
        // 不额外 toast：进度本来就画在右上角按钮与左列表角标上，多一句只是噪声。
        return recognizeMistakeRecords(ids);
    }

    function reviewViewVisible() {
        const view = el('mistakeReviewView');
        return !!view && !view.classList.contains('hidden');
    }

    /**
     * 把任务上的识别进度同步进本地状态，并刷新审校页。
     *
     * 只做一件事：把中间结果画出来。**不切换当前选中的题** —— 用户点开第 1 道就停在
     * 第 1 道，第 2 道随后识别好了也不许把界面抢过去，要看得用户自己点。这正是「像题库
     * 页」的语义：点击驱动，不自动跟随。
     */
    function applyRecognizeProgress(task) {
        const live = state.recognizeLive;
        if (Array.isArray(task.recognize_queue)) live.queue = task.recognize_queue.map(Number);
        if (Array.isArray(task.recognize_done)) live.done = task.recognize_done.map(Number);
        live.current = task.recognize_current ? Number(task.recognize_current) : null;
        live.active = true;
        if (!reviewViewVisible()) return null;
        return refreshMistakeDetail({ lightweight: true }).then(refreshReviewLive);
    }

    /** 这道题正在识别，或还排在队列里 —— 结果马上由服务端覆写，此刻不许人工编辑。 */
    function reviewRecordLocked(record) {
        if (!record) return false;
        const live = state.recognizeLive;
        if (!live || !live.active) return false;
        if (record.id === live.current) return true;
        return live.queue.indexOf(record.id) >= 0 && live.done.indexOf(record.id) < 0;
    }

    /** 「识别中 / 排队中」角标；不在本次识别队列里则返回 null（回落常规状态）。 */
    function reviewLiveStatus(record) {
        const live = state.recognizeLive;
        if (!live || !live.active || !record) return null;
        if (record.id === live.current) {
            return { label: '识别中', cls: 'bg-brand-50 text-brand-600 border-brand-200/70', spin: true };
        }
        if (live.queue.indexOf(record.id) >= 0 && live.done.indexOf(record.id) < 0) {
            return { label: '排队中', cls: 'bg-slate-100 text-slate-500 border-slate-200/70', spin: false };
        }
        return null;
    }

    /** 编辑区只读锁 + 提示条。识别中的题如果让人改，手写内容会被随后的识别结果冲掉。 */
    function syncReviewEditorLock() {
        const record = reviewDraft.recordId ? findRecord(reviewDraft.recordId) : null;
        const locked = reviewRecordLocked(record);
        ['mrContent', 'mrAnswer'].forEach(function (id) {
            const box = el(id);
            if (!box) return;
            box.disabled = locked;
            box.classList.toggle('opacity-60', locked);
        });
        const retry = el('mrRetryOneBtn');
        if (retry) {
            retry.disabled = locked;
            retry.classList.toggle('opacity-50', locked);
        }
        const hint = el('mrLockHint');
        const text = el('mrLockHintText');
        if (hint) hint.classList.toggle('hidden', !locked);
        if (text && locked) {
            text.textContent = (record.id === state.recognizeLive.current)
                ? '正在识别这道题，识别结果出来后可编辑。'
                : '这道题还在识别队列里，轮到它之前先别编辑 —— 识别结果落库会覆盖手写内容。';
        }
        return locked;
    }

    /**
     * 轮询期间刷新审校页（识别中约每秒一次）。
     *
     * 两条硬规则：
     *   1) 不切换当前选中的题 —— 识别完成的题只在左列表换角标，界面停在用户点开的那道；
     *   2) 不回填正在编辑的输入框 —— 有未保存改动就保留用户的输入，只在该题已就绪且
     *      输入框与服务端一致（即用户没在动它）时才回填。识别中/排队中的题本就只读。
     */
    function refreshReviewLive() {
        updateReviewButtons();
        renderReviewList();
        const locked = syncReviewEditorLock();
        const record = reviewDraft.recordId ? findRecord(reviewDraft.recordId) : null;
        if (!record || locked) return;
        // 分类三档要跟着识别结果走：识别刚落库时学段/章节/小节得自己切过去 ——
        // 否则用户点完「重新识别这道」，学段那里还是「选择学段」，看着像又没认出来。
        // 只在服务端那组值**相对上次回填变了**时才重填，用户手选的选择不受影响。
        if (reviewMetaTree &&
            reviewCategorySignature(record) !== (reviewDraft.syncedCategory || '')) {
            populateReviewCurriculumSelects(record);
        }
        const contentBox = el('mrContent');
        const answerBox = el('mrAnswer');
        if (contentBox.value !== (reviewDraft.syncedContent || '') ||
            answerBox.value !== (reviewDraft.syncedAnswer || '')) {
            return;   // 有未保存改动：保住用户正在敲的字
        }
        contentBox.value = record.content || '';
        answerBox.value = record.answer_markdown || '';
        reviewDraft.syncedContent = record.content || '';
        reviewDraft.syncedAnswer = record.answer_markdown || '';
        renderReviewPreview();
    }

    function updateReviewButtons() {
        const missing = reviewMissingRecords();
        const live = state.recognizeLive;
        const running = !!(live && live.active);
        const recBtn = el('mrRecognizeBtn');
        if (recBtn) {
            recBtn.classList.toggle('hidden', missing.length === 0 && !running);
            const label = recBtn.querySelector('span');
            if (label) {
                if (running) {
                    const total = live.queue.length;
                    const done = Math.min(live.done.length + (live.current ? 1 : 0), total);
                    label.textContent = '识别中 ' + done + '/' + total + '…';
                } else {
                    label.textContent = '识别未完成的 ' + missing.length + ' 道';
                }
            }
            // 识别跑着的时候按钮只报进度，不再接受第二次点击 —— 否则会并发起第二个任务。
            recBtn.disabled = running;
            recBtn.classList.toggle('opacity-50', running);
        }

        const blocked = reviewRecords().filter(function (record) {
            return !String(record.content || '').trim();
        });
        // 识别失败的原因要浮到这一页来。任务条在批次详情页头部，人站在审校页时
        // 看不到它 —— 只留一句「成功 0 道，失败 2 道」，等于没给原因。
        const failures = state.recognizeLive.errors || [];
        const failNote = failures.length
            ? '上次识别有 ' + failures.length + ' 道失败：' + failures.slice(0, 2).join('；') +
              (failures.length > 2 ? ' 等' : '') + '。'
            : '';
        const emptyNote = blocked.length
            ? '有 ' + blocked.length + ' 道题面还是空的（' + blocked.map(reviewRecordLabel).join('、') +
              '）。生成错题本会拦下它们 —— 点上面的「重新识别这道」，或右上角的「识别未完成的 N 道」，也可以直接在中间补写题面。'
            : '';
        const gate = el('mrGateBar');
        const gateText = el('mrGateText');
        if (gate && gateText) {
            gate.classList.toggle('hidden', !emptyNote && !failNote);
            gateText.textContent = failNote + emptyNote;
        }

        const gen = el('mrGenerateBtn');
        if (gen) gen.disabled = reviewRecords().length === 0;
    }

    function renderReviewList() {
        const list = el('mrList');
        if (!list) return;
        const records = reviewRecords();
        if (!records.length) {
            list.innerHTML = '<div class="p-2.5 text-[11px] text-slate-400 leading-relaxed">这批还没有勾选进错题本的题。<br><br>返回批次详情，在题卡上勾「进错题本」再进来。</div>';
            return;
        }
        list.innerHTML = records.map(function (record, index) {
            // 识别中/排队中优先于常规状态：这时候「未识别」是被动事实，「正在跑」才是
            // 用户要判断要不要等的依据。
            const live = reviewLiveStatus(record);
            const status = live || reviewRecordStatus(record);
            const active = record.id === reviewDraft.recordId;
            const preview = String(record.content || '').replace(/\s+/g, ' ').trim().slice(0, 48);
            const emptyHint = live ? (live.spin ? '识别中，稍候…' : '排队等待识别…') : '（题面为空）';
            // 行外层是 div 不是 button：行内要放一个 × 按钮，而 button 套 button 是
            // 非法 HTML，浏览器会把它们拆成两个兄弟节点，× 会跑到行外面去。
            return '<div role="button" tabindex="0" onclick="selectMistakeReviewRecord(' + record.id + ')" ' +
                'onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();selectMistakeReviewRecord(' + record.id + ');}" ' +
                'class="w-full text-left px-2.5 py-2 rounded-xl border cursor-pointer transition-all ' +
                (active
                    ? 'bg-brand-50 border-brand-300/80 dark:bg-brand-900/20 dark:border-brand-700/60'
                    : 'bg-white/70 dark:bg-slate-800/50 border-slate-200/70 dark:border-slate-700/60 hover:border-brand-200') + '">' +
                '<div class="flex items-center justify-between gap-1.5">' +
                '<span class="text-[11px] font-bold text-slate-700 dark:text-slate-200 truncate">' +
                esc((index + 1) + '. ' + reviewRecordLabel(record)) + '</span>' +
                '<span class="flex items-center gap-1 shrink-0">' +
                '<span class="text-[9px] font-bold px-1.5 py-0.5 rounded-full border ' + status.cls + '">' +
                (status.spin ? '<i class="fa-solid fa-circle-notch fa-spin mr-0.5"></i>' : '') + status.label + '</span>' +
                // 移出本次收录 ＝ 把收录开关置回 false。stopPropagation 是必须的：
                // 否则点 × 会顺带触发选中，编辑器先闪一道即将消失的题。
                '<button type="button" title="移出本次收录（回批次详情可重新勾选）" ' +
                'class="w-4 h-4 grid place-items-center rounded-md text-slate-300 dark:text-slate-600 ' +
                'hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-colors" ' +
                'onclick="event.stopPropagation();removeMistakeReviewRecord(' + record.id + ')">' +
                '<i class="fa-solid fa-xmark text-[10px]"></i></button>' +
                '</span>' +
                '</div>' +
                '<div class="mt-1 text-[10px] text-slate-400 truncate">' + esc(preview || emptyHint) + '</div>' +
                '</div>';
        }).join('');
    }

    /* ===================== 中栏「分类信息」（与题库录入表单同款） =====================
       2026-09-15 需求：审校页中栏做成题库「录入新数学题」那一套 —— 分类信息 + 题干 + 解析；
       右栏做成「题目预览」那一套 —— 上题干预览、下解析预览。

       学科差异：数学保留全套（题型/难度/来源/学段/章节/小节/知识点/解题方法/关联章节/
       自定义标签）；物理、化学只留 题型/来源/学段/章节/小节 + 关联章节。

       两条纪律：
       ① 只在内存里改，统一走防抖保存（一次 PUT 带上所有改动字段）；
       ② **选不中的历史值不许丢** —— 库里的分类可能不在当前教材树上（换过版本、
          或上一轮 AI 标的），下拉里把它补成额外一项，否则用户一保存就被静默改分类。
     */

    const REVIEW_DIFFICULTY_LABELS = {
        easy_error: '易错题', normal: '常规题', challenge: '挑战题', qiangji: '强基题'
    };
    const REVIEW_QTYPE_LABELS = {
        single_choice: '单选题', multi_choice: '多选题',
        fill_in_blank: '填空题', detailed_answer: '解答题'
    };

    function reviewCurrentSubject() {
        return String((state.batch && state.batch.subject) || 'math');
    }

    /** 数学 = 全套标签；物化只留 题型/来源/学段/章节/小节 + 关联章节。 */
    function reviewKeepsFullMeta(subject) {
        return String(subject || 'math') === 'math';
    }

    /** 按学科显隐分类字段。物化收起 难度 / 知识点 / 解题方法 / 自定义标签。 */
    function applyReviewMetaSubjectUI() {
        const full = reviewKeepsFullMeta(reviewCurrentSubject());
        ['mrDifficultyWrap', 'mrTagsRow', 'mrCustomTagsRow'].forEach(function (id) {
            const box = el(id);
            if (box) box.classList.toggle('hidden', !full);
        });
        // 难度收起来后第一行只剩「题型 + 来源」，还按三列排会在中间留个空洞
        const row = el('mrMetaRow1');
        if (row) {
            row.classList.toggle('grid-cols-3', full);
            row.classList.toggle('grid-cols-2', !full);
        }
    }

    function reviewQuestionTypeOptions() {
        const list = (state.questionTypes || []).map(function (item) {
            return { value: item.value, label: item.label || item.value };
        });
        return list.length ? list : Object.keys(REVIEW_QTYPE_LABELS).map(function (value) {
            return { value: value, label: REVIEW_QTYPE_LABELS[value] };
        });
    }

    function reviewDifficultyOptions() {
        const values = (state.difficulties || []).map(function (item) { return item.value; });
        const list = values.length ? values : Object.keys(REVIEW_DIFFICULTY_LABELS);
        return list.map(function (value) {
            return { value: value, label: reviewDifficultyLabel(value) };
        });
    }

    function reviewDifficultyLabel(value) {
        const meta = window.systemMetadata;
        if (meta && Array.isArray(meta.difficulties)) {
            const found = meta.difficulties.filter(function (item) {
                return item.value === value;
            })[0];
            if (found && found.label) return found.label;
        }
        return REVIEW_DIFFICULTY_LABELS[value] || value || '未定';
    }

    function reviewQuestionTypeLabel(value) {
        const found = (state.questionTypes || []).filter(function (item) {
            return item.value === value;
        })[0];
        if (found && (found.label || found.value)) return found.label || found.value;
        return REVIEW_QTYPE_LABELS[value] || value || '未定';
    }

    /**
     * 填一个下拉：占位项 + 候选 + （候选里没有、但库里存着的值）。
     *
     * 第三块是这条函数存在的理由：库里存着的分类可能来自换版本前的大纲，或上一轮
     * AI 标注。候选里没有就直接丢掉，用户一按保存就被静默改了分类，而且他看不见。
     */
    function reviewFillSelect(select, options, placeholder, current) {
        if (!select) return;
        const list = (options || []).slice();
        const keep = String(current || '');
        const names = list.map(function (item) { return item.value; });
        if (keep && names.indexOf(keep) < 0) list.push({ value: keep, label: keep });
        select.innerHTML = ['<option value="">' + esc(placeholder) + '</option>']
            .concat(list.map(function (item) {
                return '<option value="' + esc(item.value) + '">' + esc(item.label) + '</option>';
            })).join('');
        select.value = keep;
    }

    function reviewTree() { return reviewMetaTree || {}; }

    function reviewAsOptions(names) {
        return (names || []).map(function (name) { return { value: name, label: name }; });
    }

    function reviewCompulsoryOptions() {
        return reviewAsOptions(Object.keys(reviewTree()));
    }

    function reviewChapterOptions(compulsory) {
        return reviewAsOptions(Object.keys(reviewTree()[compulsory] || {}));
    }

    function reviewSectionOptions(compulsory, chapter) {
        return reviewAsOptions((reviewTree()[compulsory] || {})[chapter] || []);
    }

    /** 大纲长这样：{学段: {章节: [小节, …]}}。形状不对就别往下拉里灌，
     *  否则「选择学段」里会出现 status 这种字段名（接口兜底响应的键）。 */
    function reviewLooksLikeCurriculumTree(data) {
        if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
        const keys = Object.keys(data);
        if (!keys.length) return true;   // 空树是合法状态（该学科还没建目录）
        return keys.every(function (key) {
            const chapters = data[key];
            return chapters && typeof chapters === 'object' && !Array.isArray(chapters);
        });
    }

    /** 拉该学科的教材树。切批次学科时才重新请求。 */
    function loadReviewCategories() {
        const subject = reviewCurrentSubject();
        if (reviewMetaTree && reviewMetaTreeSubject === subject) {
            return Promise.resolve(reviewMetaTree);
        }
        return api('/api/categories?subject=' + encodeURIComponent(subject))
            .then(function (res) {
                const data = res && res.ok ? res.data : null;
                if (!reviewLooksLikeCurriculumTree(data)) return null;
                reviewMetaTree = data;
                reviewMetaTreeSubject = subject;
                const record = reviewDraft.recordId ? findRecord(reviewDraft.recordId) : null;
                if (record) populateReviewCurriculumSelects(record);
                return reviewMetaTree;
            });
    }

    /** 主分类三级级联 + 关联章节三级级联，都挂同一棵树。 */
    function populateReviewCurriculumSelects(record) {
        const compulsory = record ? String(record.category_compulsory || '') : '';
        const chapter = record ? String(record.category_chapter || '') : '';
        const knowledge = record ? String(record.category_knowledge || '') : '';

        reviewFillSelect(el('mrCompulsory'), reviewCompulsoryOptions(), '选择学段', compulsory);
        reviewFillSelect(el('mrChapter'), reviewChapterOptions(compulsory), '先选章节', chapter);
        reviewFillSelect(el('mrKnowledge'), reviewSectionOptions(compulsory, chapter), '先选小节（可留空）', knowledge);
        const chapterSelect = el('mrChapter');
        if (chapterSelect) chapterSelect.disabled = !compulsory;
        const sectionSelect = el('mrKnowledge');
        if (sectionSelect) sectionSelect.disabled = !chapter;

        // 关联章节用同一棵树，但从空白开始（它是「再加一条」，不是回填主分类）
        reviewFillSelect(el('mrRelCompulsory'), reviewCompulsoryOptions(), '选择学段', '');
        reviewFillSelect(el('mrRelChapter'), [], '先选章节', '');
        reviewFillSelect(el('mrRelKnowledge'), [], '先选小节（可留空）', '');
        const relChapter = el('mrRelChapter');
        if (relChapter) relChapter.disabled = true;
        const relKnowledge = el('mrRelKnowledge');
        if (relKnowledge) relKnowledge.disabled = true;
        updateReviewRelAddButton();
        // 记下「服务端此刻的分类三档」，供 refreshReviewLive 判断识别结果有没有带来
        // 新的学段/章节/小节。用快照而不是「服务端 vs 下拉当前值」：用户手选之后到
        // 保存往返之间下拉必然领先于服务端，拿当前值比会把他刚点的选择悄悄抹掉。
        reviewDraft.syncedCategory = reviewCategorySignature(record);
    }

    /** 分类三档的签名，只用来判断「服务端这一组值变了没」。 */
    function reviewCategorySignature(record) {
        return [
            record ? record.category_compulsory : '',
            record ? record.category_chapter : '',
            record ? record.category_knowledge : ''
        ].map(function (value) {
            return String(value || '');
        }).join('\u0001');
    }

    function onReviewCompulsoryChange() {
        const compulsory = (el('mrCompulsory') || {}).value || '';
        reviewFillSelect(el('mrChapter'), reviewChapterOptions(compulsory), '先选章节', '');
        reviewFillSelect(el('mrKnowledge'), [], '先选小节（可留空）', '');
        const chapterSelect = el('mrChapter');
        if (chapterSelect) chapterSelect.disabled = !compulsory;
        const sectionSelect = el('mrKnowledge');
        if (sectionSelect) sectionSelect.disabled = true;
        scheduleReviewSave();
    }

    function onReviewChapterChange() {
        const compulsory = (el('mrCompulsory') || {}).value || '';
        const chapter = (el('mrChapter') || {}).value || '';
        reviewFillSelect(
            el('mrKnowledge'), reviewSectionOptions(compulsory, chapter), '先选小节（可留空）', ''
        );
        const sectionSelect = el('mrKnowledge');
        if (sectionSelect) sectionSelect.disabled = !chapter;
        scheduleReviewSave();
    }

    function updateReviewRelAddButton() {
        const button = el('mrAddRelChapterBtn');
        if (button) button.disabled = !((el('mrRelChapter') || {}).value);
    }

    function onReviewRelCompulsoryChange() {
        const compulsory = (el('mrRelCompulsory') || {}).value || '';
        reviewFillSelect(el('mrRelChapter'), reviewChapterOptions(compulsory), '先选章节', '');
        reviewFillSelect(el('mrRelKnowledge'), [], '先选小节（可留空）', '');
        const chapterSelect = el('mrRelChapter');
        if (chapterSelect) chapterSelect.disabled = !compulsory;
        const sectionSelect = el('mrRelKnowledge');
        if (sectionSelect) sectionSelect.disabled = true;
        updateReviewRelAddButton();
    }

    function onReviewRelChapterChange() {
        const compulsory = (el('mrRelCompulsory') || {}).value || '';
        const chapter = (el('mrRelChapter') || {}).value || '';
        reviewFillSelect(
            el('mrRelKnowledge'), reviewSectionOptions(compulsory, chapter), '先选小节（可留空）', ''
        );
        const sectionSelect = el('mrRelKnowledge');
        if (sectionSelect) sectionSelect.disabled = !chapter;
        updateReviewRelAddButton();
    }

    // ---- 知识点 / 解题方法：多标签芯片（用 HTML 串拼，便于在假 DOM 里断言） ----

    function splitReviewTags(raw) {
        return String(raw || '').split(/[,，;；\n]+/)
            .map(function (token) { return token.trim(); })
            .filter(function (token) { return !!token; });
    }

    function reviewTagChipsHtml(field) {
        const tags = reviewMetaTags[field === 'solve' ? 'solve' : 'knowledge'] || [];
        return tags.map(function (tag, index) {
            return '<span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-brand-50 text-brand-700 text-[10px] font-semibold">' +
                esc(tag) +
                '<span class="cursor-pointer text-brand-400 hover:text-brand-700" title="移除" ' +
                'onclick="removeReviewMetaTag(&#39;' + (field === 'solve' ? 'solve' : 'knowledge') + '&#39;,' + index + ')">×</span>' +
                '</span>';
        }).join('');
    }

    function renderReviewMetaChips() {
        const knowledgeBox = el('mrKnowledgeTagsChips');
        if (knowledgeBox) knowledgeBox.innerHTML = reviewTagChipsHtml('knowledge');
        const solveBox = el('mrSolveMethodTagsChips');
        if (solveBox) solveBox.innerHTML = reviewTagChipsHtml('solve');
    }

    function reviewTagField(field) {
        return field === 'solve' ? 'solve' : 'knowledge';
    }

    function reviewTagInput(field) {
        return el(reviewTagField(field) === 'solve' ? 'mrSolveMethodTagInput' : 'mrKnowledgeTagInput');
    }

    function addReviewMetaTag(field, raw) {
        const key = reviewTagField(field);
        const tag = String(raw || '').replace(/^[,，;；\s]+|[,，;；\s]+$/g, '');
        if (!tag || reviewMetaTags[key].indexOf(tag) >= 0) return;
        reviewMetaTags[key].push(tag);
        renderReviewMetaChips();
        scheduleReviewSave();
    }

    /** 回车 / 逗号 提交一个标签，退格在空输入时删掉最后一个（与录入表单同款手感）。 */
    function onReviewMetaTagKey(event, field) {
        const key = reviewTagField(field);
        const input = reviewTagInput(key);
        const value = input ? String(input.value || '') : '';
        if (event && (event.key === 'Enter' || event.key === ',' || event.key === '，')) {
            if (event.preventDefault) event.preventDefault();
            addReviewMetaTag(key, value);
            if (input) input.value = '';
            return;
        }
        if (event && event.key === 'Backspace' && !value && reviewMetaTags[key].length) {
            reviewMetaTags[key].pop();
            renderReviewMetaChips();
            scheduleReviewSave();
        }
    }

    function removeReviewMetaTag(field, index) {
        const key = reviewTagField(field);
        reviewMetaTags[key].splice(Number(index), 1);
        renderReviewMetaChips();
        scheduleReviewSave();
    }

    // ---- 关联章节 ----

    function reviewRelChipsHtml() {
        return (reviewRelChapters || []).map(function (item, index) {
            const label = [
                item.compulsory, item.chapter,
                (item.knowledge && item.knowledge !== item.chapter) ? item.knowledge : ''
            ].filter(Boolean).join(' / ');
            return '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-brand-50 text-brand-700 text-[11px] font-medium border border-brand-200">' +
                esc(label) +
                '<button type="button" class="text-brand-400 hover:text-brand-700" title="移除" ' +
                'onclick="removeReviewRelatedChapter(' + index + ')"><i class="fa-solid fa-xmark"></i></button>' +
                '</span>';
        }).join('');
    }

    function renderReviewRelChips() {
        const box = el('mrRelChips');
        if (box) box.innerHTML = reviewRelChipsHtml();
    }

    function addReviewRelatedChapter() {
        const chapter = (el('mrRelChapter') || {}).value || '';
        if (!chapter) { toast('请先在关联章节里选好学段与章节', 'info'); return; }
        const item = {
            compulsory: (el('mrRelCompulsory') || {}).value || '',
            chapter: chapter,
            knowledge: (el('mrRelKnowledge') || {}).value || chapter
        };
        const duplicated = reviewRelChapters.some(function (row) {
            return row.compulsory === item.compulsory &&
                row.chapter === item.chapter && row.knowledge === item.knowledge;
        });
        if (duplicated) { toast('该关联章节已添加', 'info'); return; }
        reviewRelChapters.push(item);
        renderReviewRelChips();
        // 加完清空三级下拉：不然连点两下「添加」只会得到一条加、一条「已添加」
        reviewFillSelect(el('mrRelChapter'), [], '先选章节', '');
        reviewFillSelect(el('mrRelKnowledge'), [], '先选小节（可留空）', '');
        const chapterSelect = el('mrRelChapter');
        if (chapterSelect) chapterSelect.disabled = true;
        const sectionSelect = el('mrRelKnowledge');
        if (sectionSelect) sectionSelect.disabled = true;
        updateReviewRelAddButton();
        scheduleReviewSave();
    }

    function removeReviewRelatedChapter(index) {
        reviewRelChapters.splice(Number(index), 1);
        renderReviewRelChips();
        scheduleReviewSave();
    }

    // ---- 回填 / 收集 ----

    function renderReviewMeta(record) {
        if (!record) return;
        reviewFillSelect(el('mrQType'), reviewQuestionTypeOptions(), '选择题型', record.question_type || '');
        reviewFillSelect(el('mrDifficulty'), reviewDifficultyOptions(), '选择难度', record.difficulty || '');
        const source = el('mrSource');
        if (source) source.value = record.source || '';
        const customTags = el('mrCustomTags');
        if (customTags) customTags.value = record.tags || '';

        reviewMetaTags.knowledge = splitReviewTags(record.knowledge_tags);
        reviewMetaTags.solve = splitReviewTags(record.solve_method);
        const knowledgeInput = el('mrKnowledgeTagInput');
        if (knowledgeInput) knowledgeInput.value = '';
        const solveInput = el('mrSolveMethodTagInput');
        if (solveInput) solveInput.value = '';
        renderReviewMetaChips();

        reviewRelChapters = (Array.isArray(record.related_curriculums) ? record.related_curriculums : [])
            .map(function (item) {
                return {
                    compulsory: String((item && item.compulsory) || ''),
                    chapter: String((item && item.chapter) || ''),
                    knowledge: String((item && item.knowledge) || '')
                };
            })
            .filter(function (item) { return !!item.chapter; });
        renderReviewRelChips();

        populateReviewCurriculumSelects(record);
        reviewDraft.syncedMeta = JSON.stringify(collectReviewMeta());
    }

    function collectReviewMeta() {
        return {
            question_type: (el('mrQType') || {}).value || '',
            difficulty: (el('mrDifficulty') || {}).value || '',
            source: (el('mrSource') || {}).value || '',
            category_compulsory: (el('mrCompulsory') || {}).value || '',
            category_chapter: (el('mrChapter') || {}).value || '',
            category_knowledge: (el('mrKnowledge') || {}).value || '',
            knowledge_tags: reviewMetaTags.knowledge.join(','),
            solve_method: reviewMetaTags.solve.join(','),
            tags: (el('mrCustomTags') || {}).value || '',
            related_curriculums: reviewRelChapters.map(function (item) {
                return {
                    compulsory: item.compulsory || '',
                    chapter: item.chapter || '',
                    knowledge: item.knowledge || ''
                };
            })
        };
    }

    /** 只把真正改过的字段发出去 —— 全量发会把没动过的字段也写一遍，出问题难定位。 */
    function reviewMetaPatch(current, synced) {
        const patch = {};
        Object.keys(current).forEach(function (key) {
            if (JSON.stringify(current[key]) !== JSON.stringify(synced[key])) {
                patch[key] = current[key];
            }
        });
        return patch;
    }

    const REVIEW_PREVIEW_TONES = {
        slate: 'bg-slate-100 text-slate-700',
        brand: 'bg-brand-50 text-brand-700',
        emerald: 'bg-emerald-50 text-emerald-700'
    };

    function reviewPreviewBadge(text, tone) {
        return '<span class="text-[10px] font-bold px-2 py-0.5 rounded-md ' +
            (REVIEW_PREVIEW_TONES[tone] || REVIEW_PREVIEW_TONES.slate) + '">' + esc(text) + '</span>';
    }

    function reviewDifficultyBadgeClass(value) {
        let classes = '';
        if (typeof window.getDifficultyColor === 'function') {
            try { classes = String(window.getDifficultyColor(value) || ''); }
            catch (err) { classes = ''; }
        }
        // 只留类名允许的字符：色值来自「设置 - 难度」里可编辑的词表，
        // 原样拼进 class 属性等于把引号/尖括号一起放进来。
        return classes.replace(/[^\w\-\/\s]/g, ' ').trim() || REVIEW_PREVIEW_TONES.slate;
    }

    /**
     * 右栏预览：照题库「题目预览」的仿真试卷卡片。
     * 上半题干、下半「参考答案与详细解析」——用户 2026-09-15 明确要的上下两段。
     * 2026-09-16：去掉卡片顶部的品牌色条与「本地教研系统」抬头（用户指出多余），
     * 卡片直接从徽章行起。页脚来源行保留。
     */
    function reviewPaperHtml(record, content, answer) {
        const badges = [
            reviewPreviewBadge('编号：' + (record.question_no || '未定'), 'slate'),
            reviewPreviewBadge('题型：' + reviewQuestionTypeLabel(record.question_type), 'brand')
        ];
        badges.push('<span class="text-[10px] font-bold px-2 py-0.5 rounded-md ' +
            reviewDifficultyBadgeClass(record.difficulty) + '">难度：' +
            esc(reviewDifficultyLabel(record.difficulty)) + '</span>');

        const answerBody = answer.trim()
            ? '<div class="text-[12px] text-slate-700 dark:text-slate-200 leading-relaxed">' +
                contentPreviewHtml(answer, record.answer_images, 'answer_images') + '</div>'
            : '<p class="text-slate-400 italic text-xs">暂无解析内容。</p>';
        const hidden = reviewDraft.answerHidden;

        return '<div class="border border-slate-300 rounded-2xl shadow-lg paper-card overflow-hidden min-h-[420px] flex flex-col select-text">' +
            '<div class="p-4 flex-1 space-y-4">' +
                '<div class="flex flex-wrap gap-1.5">' + badges.join('') + '</div>' +
                // 上：题干
                '<div class="text-sm leading-relaxed text-slate-800 dark:text-slate-100">' +
                    contentPreviewHtml(content, record.figure_images, 'figure_images') +
                '</div>' +
                // 下：解析
                '<div class="border-t border-dashed border-slate-200 pt-3 space-y-2">' +
                    '<div class="flex justify-between items-center gap-2">' +
                        '<h5 class="text-[11px] font-extrabold tracking-wider text-slate-400 flex items-center space-x-1.5">' +
                            '<i class="fa-solid fa-signature text-brand-500"></i><span>参考答案与详细解析</span></h5>' +
                        '<button type="button" onclick="toggleMrPreviewAnswer()" ' +
                            'class="flex items-center space-x-1 px-2 py-0.5 bg-brand-50 hover:bg-brand-100 text-brand-700 rounded-lg text-[11px] font-bold transition-all border border-brand-100">' +
                            '<i class="fa-solid ' + (hidden ? 'fa-eye' : 'fa-eye-slash') + '"></i>' +
                            '<span>' + (hidden ? '显示解析' : '隐藏解析') + '</span>' +
                        '</button>' +
                    '</div>' +
                    '<div id="mrAnalysisBody" class="p-3 rounded-xl bg-slate-50 border border-slate-200 leading-relaxed' +
                        (hidden ? ' hidden' : '') + '">' + answerBody + '</div>' +
                '</div>' +
            '</div>' +
            '<div class="px-4 py-2 border-t border-slate-200 bg-slate-50/60 flex items-center justify-between text-[10px] text-slate-400">' +
                '<span>来源：' + esc(record.source || '本地教研录入') + '</span>' +
                '<span>小陈的数学宝藏</span>' +
            '</div>' +
        '</div>';
    }

    function toggleMrPreviewAnswer() {
        reviewDraft.answerHidden = !reviewDraft.answerHidden;
        renderReviewPreview();
    }

    function renderReviewPreview() {
        const box = el('mrPreview');
        if (!box) return;
        updateMrPlaceholderButton();
        const record = reviewDraft.recordId ? findRecord(reviewDraft.recordId) : null;
        if (!record) { box.innerHTML = ''; return; }
        const content = String(record.content || '');
        const answer = String(record.answer_markdown || '');
        if (!content.trim()) {
            box.innerHTML = '<div class="text-[11px] text-amber-600 leading-relaxed">题面为空。识别没跑过或失败时就是这种状态 —— 先在中间补上题面，再生成错题本。</div>';
            return;
        }
        box.innerHTML = reviewPaperHtml(record, content, answer) + reviewFigureStripHtml(record);
        renderMathIn(box);
    }

    function selectReviewRecord(recordId) {
        if (!recordId) return;
        bindReviewCursorTracking();
        flushReviewSave();
        reviewDraft.recordId = recordId;
        reviewDraft.cursor = null;
        // 上一道题浮出来的「补这张图」不能跟过来：它带的序号指的是上一道的第 N 个占位符。
        mrHidePlaceholderActions();
        const record = findRecord(recordId);
        if (!record) return;
        el('mrContent').value = record.content || '';
        el('mrAnswer').value = record.answer_markdown || '';
        el('mrDocLabel').textContent = record.question_no ? ('# ' + record.question_no) : '';
        // 上一条的「保存中…／已保存／保存失败」不能跟过来：那句文案说的是上一条，
        // 挂在新题上等于谎报。本条真正保存完成时 flushReviewSave 会再写。
        const saveLabel = el('mrSaveState');
        if (saveLabel) saveLabel.textContent = '';
        reviewDraft.syncedContent = record.content || '';
        reviewDraft.syncedAnswer = record.answer_markdown || '';
        // 解析区默认展开：换题时上一个的「隐藏解析」状态不该跟过来
        reviewDraft.answerHidden = false;
        // 识别区也回初始态：不能留着上一题那张截图的缩略图和「已追加 N 字」
        setReviewOcrStage('idle');
        renderReviewMeta(record);
        renderReviewList();
        renderReviewPreview();
        updateReviewButtons();
        syncReviewEditorLock();
    }

    /** 左列表被剔空时把中/右栏清干净 —— 留着一道已经不在列表里的题，人会以为它还在收录。 */
    function clearReviewEditor() {
        reviewDraft.recordId = null;
        reviewDraft.syncedContent = '';
        reviewDraft.syncedAnswer = '';
        el('mrContent').value = '';
        el('mrAnswer').value = '';
        el('mrDocLabel').textContent = '';
        el('mrPreview').innerHTML = '';
        reviewDraft.syncedMeta = '';
        renderReviewList();
        updateReviewButtons();
        syncReviewEditorLock();
    }

    /**
     * 审校页行内「移出本次收录」（2026-09-18 需求）。
     *
     * 只做一件事：把收录开关置回 false。这一个开关同时管三处出口 —— 本页左列表
     * （reviewRecords）、入库（generateReviewHandout 只送本页列出的 id）、错题本 PDF
     * （main.py 只取 include_in_handout=True）—— 所以置 false 就等于「这道题本次不要」。
     *
     * 不删记录：识别结果、分类、解析全部留着。回批次详情页重新勾「进错题库」即可原样
     * 回来；题库里已经入库的副本也不受影响（那是题库删题，另一回事）。
     */
    function removeMistakeReviewRecord(recordId) {
        const record = findRecord(recordId);
        if (!record) return;
        const label = reviewRecordLabel(record);
        const inBank = !!record.question_id;
        // 正在编辑的那道被移出：先把草稿落盘，否则移除后没人再触发保存，用户刚敲的
        // 字留在 textarea 里就丢了。
        if (reviewDraft.recordId === recordId) flushReviewSave();
        const index = reviewRecords().findIndex(function (item) { return item.id === recordId; });
        patchRecord(recordId, { include_in_handout: false }, { silent: true }).then(function (updated) {
            if (!updated) return;   // 失败已由 patchRecord 弹提示，界面状态不动
            toast('已移出本次收录：' + label + '。' +
                (inBank ? '题库里已入库的那道不受影响。' : '') +
                '需要的话回批次详情页重新勾「进错题库」。');
            // 批次详情页的统计里有一项就是收录数，跟着改
            renderStats();
            const rest = reviewRecords();
            if (reviewDraft.recordId !== recordId) {
                // 移出的不是当前编辑的题：列表重绘即可，中/右栏不动。
                renderReviewList();
                return;
            }
            if (!rest.length) {
                clearReviewEditor();
                return;
            }
            // 顺位选中它后面那道（已经是最后一道就取当前的最后一道），编辑器不落空。
            const next = rest[Math.min(index < 0 ? 0 : index, rest.length - 1)];
            selectReviewRecord(next.id);
        });
    }

    /** 防抖静默保存：题面与解析一起提交，避免两次 PUT 互相覆盖。 */
    function scheduleReviewSave() {
        const label = el('mrSaveState');
        if (label) label.textContent = '未保存…';
        if (reviewDraft.timer) clearTimeout(reviewDraft.timer);
        reviewDraft.timer = setTimeout(flushReviewSave, 700);
    }

    /**
     * 把「屏幕上的值」乐观写回本地记录与同步基准，返回一份可还原的快照。
     *
     * **为什么必须乐观写回**：PUT 有往返窗口（本机也要几十毫秒）。窗口内只要发生重绘
     * —— 用户改完立刻点左侧列表项、或识别轮询触发 refreshReviewLive —— 读到的都是
     * `state.records` 里那份**还没收到响应**的旧记录，于是：
     *   - 点回同一条：输入框被旧值覆盖，屏幕回滚（库里其实是对的）；
     *   - 切到另一条：旧题的响应回来把 synced* 写成旧题的值，新题的识别结果比对基准
     *     被污染，**永远回填不上**，还会显示「已保存」指向错误的题。
     * 先写回本地，往返窗口里任何重绘读到的都是用户最新值，两个症状一起消失。
     */
    function applyReviewDraftLocally(recordId, content, answer, metaPatch, meta) {
        const record = findRecord(recordId);
        const snapshot = {
            record: record,
            content: record ? record.content : undefined,
            answer: record ? record.answer_markdown : undefined,
            fields: {},
            syncedContent: reviewDraft.syncedContent,
            syncedAnswer: reviewDraft.syncedAnswer,
            syncedMeta: reviewDraft.syncedMeta
        };
        if (record) {
            record.content = content;
            record.answer_markdown = answer;
            Object.keys(metaPatch).forEach(function (key) {
                snapshot.fields[key] = record[key];
                record[key] = metaPatch[key];
            });
        }
        reviewDraft.syncedContent = content;
        reviewDraft.syncedAnswer = answer;
        reviewDraft.syncedMeta = JSON.stringify(meta);
        return snapshot;
    }

    /** 保存失败时还原乐观写回。记录本身一定要还原（否则这道题会被当成「已保存」），
     *  同步基准只在还停在这条记录上时还原 —— 切走了的话 synced* 已经属于另一道题。 */
    function rollbackReviewDraftLocally(recordId, snapshot) {
        const record = snapshot && snapshot.record;
        if (record) {
            record.content = snapshot.content;
            record.answer_markdown = snapshot.answer;
            Object.keys(snapshot.fields).forEach(function (key) {
                record[key] = snapshot.fields[key];
            });
        }
        if (reviewDraft.recordId !== recordId) return;
        reviewDraft.syncedContent = snapshot.syncedContent;
        reviewDraft.syncedAnswer = snapshot.syncedAnswer;
        reviewDraft.syncedMeta = snapshot.syncedMeta;
    }

    function flushReviewSave() {
        if (reviewDraft.timer) { clearTimeout(reviewDraft.timer); reviewDraft.timer = null; }
        const recordId = reviewDraft.recordId;
        if (!recordId) return null;
        const record = findRecord(recordId);
        if (!record) return null;
        // 正在识别/排队的题不许写回：服务端马上要用识别结果覆写这条记录，这时把人手
        // 改的版本发上去只会被后到的结果冲掉，等于白改一次。
        if (reviewRecordLocked(record)) return null;
        const content = el('mrContent').value;
        const answer = el('mrAnswer').value;
        const meta = collectReviewMeta();
        let syncedMeta = {};
        try { syncedMeta = reviewDraft.syncedMeta ? JSON.parse(reviewDraft.syncedMeta) : {}; }
        catch (err) { syncedMeta = {}; }
        const metaPatch = reviewMetaPatch(meta, syncedMeta);
        const contentChanged = content !== (record.content || '');
        const answerChanged = answer !== (record.answer_markdown || '');
        if (!contentChanged && !answerChanged && !Object.keys(metaPatch).length) return null;

        const label = el('mrSaveState');
        if (label) label.textContent = '保存中…';
        const snapshot = applyReviewDraftLocally(recordId, content, answer, metaPatch, meta);
        // 一次 PUT 提交全部改动：题面/解析/分类字段分两笔发，后一笔会覆盖前一笔
        // （PUT 是整条记录的语义，两笔并发时谁都可能先到）。
        const body = Object.assign({}, metaPatch);
        if (contentChanged) body.content = content;
        if (answerChanged) body.answer_markdown = answer;
        return patchRecord(recordId, body, { silent: true })
            .then(function (updated) {
                if (!updated) {
                    // 失败就不能留着「乐观写回」的假象：还原基准，让下一次编辑重发。
                    rollbackReviewDraftLocally(recordId, snapshot);
                    if (reviewDraft.recordId === recordId && label) label.textContent = '保存失败';
                    return null;
                }
                // 响应回来时用户可能已经切到别的题了。这时只能让 patchRecord 更新
                // 那一题的数据，绝不能碰当前界面与 synced*（否则会串味到新题上）。
                if (reviewDraft.recordId !== recordId) return updated;
                if (label) label.textContent = '已保存';
                reviewDraft.syncedContent = content;
                reviewDraft.syncedAnswer = answer;
                reviewDraft.syncedMeta = JSON.stringify(meta);
                renderReviewList();
                renderReviewPreview();
                updateReviewButtons();
                return updated;
            });
    }

    /* ===================== 解析截图 OCR =====================
       2026-09-16 需求：解析处引入 OCR 识别 —— 手头零散的解析/答案截图，不必先跑整卷识别，
       直接上传、拖入或 Ctrl+V 粘贴，识别结果按「追加 / 替换」写进解析框并落库。
       走的是与题库「OCR 图像识别」同一个 POST /api/ocr（令牌由 api.js 的 fetch 补丁自动带上），
       文本清洗也复用 ocr.js 的 cleanMathOcrText —— ocr.js 是经典脚本，顶层 function 声明
       本身就是全局，所以直接取 window 上那个，不另抄一份。 */

    const REVIEW_OCR_MODE_KEY = 'mathbank_review_ocr_mode';
    let reviewAnswerOcrAbort = null;

    function reviewOcrMode() {
        try {
            return localStorage.getItem(REVIEW_OCR_MODE_KEY) === 'replace' ? 'replace' : 'append';
        } catch (err) {
            return 'append';
        }
    }

    function renderReviewOcrModeUI() {
        const mode = reviewOcrMode();
        [['mrOcrModeAppend', 'append'], ['mrOcrModeReplace', 'replace']].forEach(function (pair) {
            const btn = el(pair[0]);
            if (!btn) return;
            const on = mode === pair[1];
            btn.classList.toggle('bg-white', on);
            btn.classList.toggle('dark:bg-slate-700', on);
            btn.classList.toggle('text-brand-600', on);
            btn.classList.toggle('shadow-sm', on);
            btn.classList.toggle('text-slate-400', !on);
        });
    }

    function setReviewOcrMode(mode) {
        const next = mode === 'replace' ? 'replace' : 'append';
        try { localStorage.setItem(REVIEW_OCR_MODE_KEY, next); } catch (err) { /* 无痕模式等，忽略 */ }
        renderReviewOcrModeUI();
        toast(next === 'replace' ? '识别结果将覆盖原解析' : '识别结果将追加到解析末尾', 'info');
    }

    /** 三态：idle（可上传）/ busy（识别中）/ done（已识别，可换图）。 */
    function setReviewOcrStage(stage, text, thumbUrl) {
        const idle = el('mrAnswerOcrIdle');
        const busy = el('mrAnswerOcrBusy');
        const done = el('mrAnswerOcrDone');
        if (idle) idle.classList.toggle('hidden', stage !== 'idle');
        if (busy) busy.classList.toggle('hidden', stage !== 'busy');
        if (done) done.classList.toggle('hidden', stage !== 'done');
        if (stage === 'busy' && text && el('mrAnswerOcrBusyText')) {
            el('mrAnswerOcrBusyText').textContent = text;
        }
        if (stage === 'done') {
            if (text && el('mrAnswerOcrDoneText')) el('mrAnswerOcrDoneText').textContent = text;
            if (thumbUrl && el('mrAnswerOcrThumb')) el('mrAnswerOcrThumb').src = thumbUrl;
        }
    }

    /** 按当前模式写进解析框。走与手敲同一条保存路径（防抖 → 一次 PUT），不另发请求。 */
    function applyReviewAnswerOcrText(text) {
        const area = el('mrAnswer');
        if (!area) return;
        const clean = String(text == null ? '' : text);
        if (reviewOcrMode() === 'replace') {
            area.value = clean;
        } else {
            const cur = String(area.value || '');
            area.value = cur.trim() ? (cur.replace(/\s+$/, '') + '\n\n' + clean) : clean;
        }
        scheduleReviewSave();
    }

    function runReviewAnswerOcr(file) {
        if (!file || !file.type || file.type.indexOf('image/') !== 0) {
            toast('请提供有效的图片（png / jpg / 截图）', 'error');
            return;
        }
        const record = reviewDraft.recordId ? findRecord(reviewDraft.recordId) : null;
        if (!record) { toast('先在左侧选一道题，再把解析截图给它', 'info'); return; }
        // 正在识别这道题时服务端会用识别结果覆写记录，这时写进去等于白写。
        if (reviewRecordLocked(record)) { toast('这道题正在识别中，等它跑完再用截图识别。', 'info'); return; }

        if (reviewAnswerOcrAbort) reviewAnswerOcrAbort.abort();
        reviewAnswerOcrAbort = new AbortController();
        const signal = reviewAnswerOcrAbort.signal;

        // 缩略图先上屏：让用户看得见自己粘的是哪一张，认错了当场换。
        let thumb = '';
        const reader = new FileReader();
        reader.onload = function (e) {
            thumb = String((e.target && e.target.result) || '');
            if (thumb && el('mrAnswerOcrThumb')) el('mrAnswerOcrThumb').src = thumb;
        };
        reader.readAsDataURL(file);
        setReviewOcrStage('busy', '正在识别解析截图…', '');

        const form = new FormData();
        form.append('file', file);
        form.append('engine', 'default');   // 跟随系统 OCR_PREFER_ENGINE，与题库识别同一引擎

        fetch('/api/ocr', { method: 'POST', body: form, signal: signal })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                reviewAnswerOcrAbort = null;
                if (!data || data.status !== 'success') {
                    setReviewOcrStage('idle');
                    toast('解析识别失败：' + ((data && data.message) || '服务端未返回结果'), 'error');
                    return;
                }
                const raw = String(data.latex || '');
                // 空判定必须对着引擎**原文**：清洗器只要吐出任何非空白字符（前后缀、
                // 标记等），「清洗后为空」就不是「引擎什么都没认出来」，空结果会被当
                // 有效内容写进解析框。反过来，清洗万一把内容吃光了，退回原文而不是写空。
                if (!raw.trim()) {
                    setReviewOcrStage('idle');
                    toast('识别结果为空，换一张更清晰的截图试试', 'error');
                    return;
                }
                let clean = (typeof window.cleanMathOcrText === 'function')
                    ? window.cleanMathOcrText(raw) : raw;
                if (!String(clean).trim()) clean = raw;
                applyReviewAnswerOcrText(clean);
                setReviewOcrStage('done', '已' + (reviewOcrMode() === 'replace' ? '替换' : '追加') +
                    ' ' + String(clean).trim().length + ' 字', thumb);
                toast('解析已识别并写入（保存中…）');
            })
            .catch(function (err) {
                if (err && err.name === 'AbortError') return;   // 主动换图/取消，不当错误报
                reviewAnswerOcrAbort = null;
                setReviewOcrStage('idle');
                toast('解析识别出错：' + err, 'error');
            });
    }

    /** 识别区事件：点击 / 拖入 / 键盘。粘贴由 ocr.js 的全局 paste 路由（E-2）转进来。 */
    function bindReviewAnswerOcr() {
        const zone = el('mrAnswerOcrZone');
        const input = el('mrAnswerOcrFile');
        if (!zone || !input) return;
        // 文件输入是识别区的子元素，programmatic click 会冒泡回本处理器。
        // 浏览器靠 click-in-progress 标志兜住（bindDropZone 那边同款写法就靠它），
        // 这里显式挡一下，顺序不依赖那个标志。
        zone.addEventListener('click', function (e) {
            if (e && e.target === input) return;
            input.click();
        });
        zone.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); zone.click(); }
        });
        input.addEventListener('change', function () {
            if (input.files && input.files.length) runReviewAnswerOcr(input.files[0]);
            input.value = '';   // 重置，同一张图再选一次也要能触发
        });
        ['dragenter', 'dragover'].forEach(function (name) {
            zone.addEventListener(name, function (e) {
                e.preventDefault();
                zone.classList.add('border-brand-500');
            });
        });
        ['dragleave', 'drop'].forEach(function (name) {
            zone.addEventListener(name, function (e) {
                e.preventDefault();
                zone.classList.remove('border-brand-500');
            });
        });
        zone.addEventListener('drop', function (e) {
            const files = (e.dataTransfer && e.dataTransfer.files) || [];
            if (files.length) runReviewAnswerOcr(files[0]);
        });
    }

    function openMistakeReview() {
        if (!state.batch) { toast('请先打开一个错题批次', 'error'); return; }
        flushReviewSave();
        el('mistakeListView').classList.add('hidden');
        el('mistakeDetailView').classList.add('hidden');
        const view = el('mistakeReviewView');
        if (view) view.classList.remove('hidden');
        // 学科决定分类字段的多少（物化只留 6 项），教材树也按学科取
        applyReviewMetaSubjectUI();
        loadReviewCategories();
        // 「追加 / 替换」是全局偏好，进页面时按记住的值点亮
        renderReviewOcrModeUI();

        const records = reviewRecords();
        el('mrTitle').textContent = (state.batch.title || '错题本') + ' · 审校';
        el('mrMeta').textContent = (SUBJECT_LABELS[state.batch.subject || 'math'] || '数学') +
            ' · 本次收录 ' + records.length + ' 道';
        const label = el('mrSaveState');
        if (label) label.textContent = '';

        if (!records.length) {
            clearReviewEditor();
            return;
        }
        const keep = records.some(function (r) { return r.id === reviewDraft.recordId; })
            ? reviewDraft.recordId : records[0].id;
        reviewDraft.recordId = null;
        selectReviewRecord(keep);
        // 识别可能是在批次详情页起的头，那时审校页还隐藏着、进度刷新被跳过；
        // 这里补一次拉取，之后每个轮询周期就会自动接力。
        if (state.recognizeLive.active) refreshMistakeDetail().then(refreshReviewLive);
        autoRecognizeOnReviewOpen();
    }

    function closeMistakeReview() {
        flushReviewSave();
        // 清掉草稿指向。留着它，下次进来会先拿旧记录跑一次 flushReviewSave —— 那时候
        // textarea 里还停着上一轮的文字，等于把 A 题的改动写进 B 题。而且那道题可能已经
        // 改了收录状态甚至已入库，停在它上面也没有意义。
        reviewDraft.recordId = null;
        reviewDraft.syncedContent = '';
        reviewDraft.syncedAnswer = '';
        reviewDraft.syncedMeta = '';
        const view = el('mistakeReviewView');
        if (view) view.classList.add('hidden');
        el('mistakeDetailView').classList.remove('hidden');
        // 识别还在跑的时候详情页走的是轻量刷新（不重绘题卡），返回时补一次全量，
        // 让卡片上的识别状态角标跟上服务端。识别已经结束时不必补 —— onDone 那次
        // 本来就是全量，再拉一次纯属白跑。
        if (state.recognizeLive.active) refreshMistakeDetail();
    }

    function recognizeMissingForReview() {
        flushReviewSave();
        const ids = reviewMissingRecords().map(function (record) { return record.id; });
        if (!ids.length) { toast('没有需要识别的题目', 'info'); return; }
        toast('正在识别 ' + ids.length + ' 道，每完成一道就能点开查看…', 'info');
        recognizeMistakeRecords(ids, function () {
            // 收尾统一走 refreshReviewLive：它自己会判断只读锁与未保存改动，
            // 不再无条件把服务端内容倒进 textarea（那样会冲掉用户正在敲的字）。
            refreshMistakeDetail().then(refreshReviewLive);
        });
    }

    /** 审校页「重新识别这道」：单题重试，识别失败或认错时用。 */
    function recognizeCurrentReviewRecord() {
        const recordId = reviewDraft.recordId;
        if (!recordId) { toast('先在左侧选一道题', 'info'); return; }
        if (state.recognizeLive.active) {
            toast('识别正在进行中，等它跑完再重试这道。', 'info');
            return;
        }
        // 复用单题识别：队列里只有这一道，只读锁与「识别中」角标都按既有规则走。
        recognizeOneMistake(Number(recordId));
        toast('正在重新识别这道题…', 'info');
    }

    /* ===================== 正文内联图片 / 占位符 =====================
       2026-09-15 需求：图片插在**正文里的位置**（光标处 / 占位符处），不再往记录尾部追加。
       识别阶段写下的 `[插图待补: 图N]` 因此变成可点芯片：点一下框选，补完就地换成图片。
       导出侧认得同一套 markdown 图片语法，图片位置随之固定下来。 */

    // `[插图待补: 图1]` / `[插图待补：图2]` —— 模型两种冒号都写过，都要认。
    const MR_PLACEHOLDER_RE = /\[插图待补\s*[:：]\s*[^\]]*\]/g;

    function mrPlaceholderCount(text) {
        const found = String(text || '').match(MR_PLACEHOLDER_RE);
        return found ? found.length : 0;
    }

    /** 把第 index 个（0 起）占位符换成 replacement；那个位置已经没有占位符时返回 null。 */
    function mrReplacePlaceholder(text, index, replacement) {
        let seen = 0;
        let hit = false;
        const out = String(text || '').replace(MR_PLACEHOLDER_RE, function (match) {
            if (hit) return match;
            if (seen === index) { hit = true; return replacement; }
            seen += 1;
            return match;
        });
        return hit ? out : null;
    }

    /** 在 pos 处插入一段 markdown 图片，需要时补足空行，免得图片跟正文挤在同一行。 */
    function mrInsertMarkdownImage(text, pos, markdown) {
        const source = String(text || '');
        const at = (typeof pos === 'number' && pos >= 0) ? Math.min(pos, source.length) : source.length;
        const before = source.slice(0, at);
        const after = source.slice(at);
        const lead = /(^|\n)\s*$/.test(before) ? '' : '\n\n';
        const tail = /^\s*(\n|$)/.test(after) ? '' : '\n\n';
        return { text: before + lead + markdown + tail + after, caret: (before + lead + markdown).length };
    }

    function mrImageMarkdown(url) {
        return '![插图](' + String(url || '') + ')';
    }

    // 正文里的 markdown 图片：**必须**与预览渲染用的是同一条正则。
    // 2026-09-21 之前这里另写了一份（多了 \s*），而预览那份不带 \s* ——
    // 遇到 `![插图]( /a.png )` 这种带空格的写法，预览认不出、这里却认得出，
    // 于是移除按钮的序号和预览里看到的第几张图对不上，**点掉的会是另一张图**。
    // 现在统一引用 MathRender 里那一份，从根上掐掉对不齐。
    const MR_INLINE_IMAGE_RE = window.MathRender.INLINE_IMAGE_RE;

    /** 摘掉正文里第 index 个（0 起）内联图；那个位置没有图时返回 null。 */
    function mrRemoveNthInlineImage(text, index) {
        let seen = -1;
        let hit = false;
        const out = String(text || '').replace(MR_INLINE_IMAGE_RE, function (match) {
            if (hit) return match;
            seen += 1;
            if (seen === index) { hit = true; return ''; }
            return match;
        });
        return hit ? mrTidyBlankLines(out) : null;
    }

    /** 预览里内联图的移除入口：把对应那段 markdown 从正文里摘掉。 */
    function removeInlineMistakeImage(target, index) {
        const record = reviewDraft.recordId ? findRecord(reviewDraft.recordId) : null;
        if (!record) return;
        const isAnswer = target === 'answer_images';
        const area = el(isAnswer ? 'mrAnswer' : 'mrContent');
        const field = isAnswer ? 'answer_markdown' : 'content';
        const source = area ? String(area.value || '') : String(record[field] || '');
        const next = mrRemoveNthInlineImage(source, index);
        if (next === null) { toast('这张图已经不在正文里了', 'info'); return; }
        const payload = {};
        payload[field] = next;
        patchRecord(record.id, payload).then(function () {
            if (area) area.value = next;
            if (record.id === reviewDraft.recordId) {
                if (isAnswer) reviewDraft.syncedAnswer = next;
                else reviewDraft.syncedContent = next;
            }
            toast('已从正文里移除这张图');
            renderReviewList();
            renderReviewPreview();
            updateReviewButtons();
        });
    }

    /** 未补配图的占位符 → 预览里的**静态标记**（2026-09-18 起不再可点）。
     *
     *  它只回答「这一处还缺图」；补图入口在源码区 —— 把光标点到源码里的 `[插图待补: 图N]`
     *  上，该行旁边会浮出「补这张图」。
     *
     *  ``label`` 取自**已经过 ``esc`` 的**预览 html，所以这里不能再转义一次，否则
     *  `&lt;` 会显示成看得见的 `&amp;lt;`。（源码区那个按钮正好相反，见
     *  ``mrPlaceholderActionHtml``。） */
    function mrPlaceholderChipHtml(label) {
        return '<span class="inline-flex items-center gap-1 my-0.5 px-1.5 py-0.5 rounded-md border border-amber-300 bg-amber-50 text-amber-700 text-[11px]"' +
            ' title="这一处还缺图：把左侧源码里的光标点到这个占位符上，即可打开原卷截图补图">' +
            '［插图待补：' + label + '］</span>';
    }

    /* ---------------- 源码区的占位符入口（2026-09-18） ----------------

       需求原文：「这里的插图待补的跳转，我不希望是在这个预览的地方，我原本是希望在 latex
       源码编辑的位置点击然后跳转到截图处」。入口因此从预览搬进源码区：

       - 预览里的标记只剩「让人看见这一处还缺图」，点了不再弹窗；
       - 把光标点到源码里的 `[插图待补: 图N]` 上，就浮出「补这张图」，点它打开**原卷整页
         截图**（沿用原有框选弹窗，它会自动跳到本题所在页并滚到本题位置）。

       textarea 是纯文本控件，**放不进可点元素**，所以「点了哪个占位符」只能按光标位反推
       —— 光标位本来就有（``bindReviewCursorTracking`` 记的，补图落点也在用），直接复用。 */

    /** 源码区输入框 ↔ 补图目标 / 浮层元素的对应关系。 */
    const MR_FIGURE_ACTION_AREAS = {
        mrContent: { target: 'figure_images', actionId: 'mrContentFigureAction' },
        mrAnswer: { target: 'answer_images', actionId: 'mrAnswerFigureAction' }
    };

    /** 光标落在哪个占位符上？返回它在**本篇文本里的序号**（0 起）与起止位置；光标不在任何
     *  占位符上时返回 null。
     *
     *  序号口径必须与 ``mrReplacePlaceholder`` 一致（都按出现顺序数），否则会「点了第 2 个
     *  却换掉第 1 个」—— 这类错位不报错，只是图补到了别的位置上。 */
    function mrPlaceholderAt(text, pos) {
        const source = String(text || '');
        if (typeof pos !== 'number' || pos < 0) return null;
        const re = new RegExp(MR_PLACEHOLDER_RE.source, 'g');
        let match;
        let index = 0;
        while ((match = re.exec(source)) !== null) {
            const start = match.index;
            const end = start + match[0].length;
            // 光标落在令牌之内、或紧贴右括号，都算「点在它上面」。
            if (pos >= start && pos <= end) {
                const inner = match[0].replace(/^\[插图待补\s*[:：]\s*/, '').replace(/\]$/, '');
                return { index: index, label: inner.trim(), start: start, end: end };
            }
            index += 1;
            if (re.lastIndex === match.index) re.lastIndex += 1;   // 空匹配保护，别死循环
        }
        return null;
    }

    /** 源码占位符旁浮出的行动按钮。
     *
     *  ``label`` 来自 textarea 的**原始文本**（没经过 esc），所以这里必须转义 —— 与预览
     *  芯片正好相反。 */
    function mrPlaceholderActionHtml(target, info) {
        return '<button type="button"' +
            // 按下就掐掉焦点转移：textarea 一 blur 浮层就会被收起，click 根本落不到这个按钮上。
            // Safari 不保证认 mousedown 的 preventDefault，所以收起侧另留了延迟兜底。
            ' onmousedown="event.preventDefault()"' +
            ' onclick="openMistakeCropModalFromPlaceholder(\'' + target + '\',' + info.index + ')"' +
            ' title="打开原卷截图，框选后把这个占位符就地换成图片"' +
            ' class="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-amber-300 bg-amber-50 text-amber-800 text-[11px] font-medium shadow-lg hover:bg-amber-100 transition-colors">' +
            '<i class="fa-solid fa-crop-simple text-[10px]"></i>补这张图' +
            '<span class="text-amber-600">' + esc(info.label) + '</span></button>';
    }

    /** 把行动按钮摆到**占位符那一行**上。
     *
     *  纵向要精确到行，只能让浏览器替我们算换行：在一个不可见的镜像 div 里复刻 textarea
     *  的排版参数，把占位符之前那段文本灌进去，量它的 rect。横向则一律贴右缘 —— 要精确到
     *  列就得连字体、滚动条宽度都复刻到位，差一点就偏到别的列上；而「同一行」这个语义只要
     *  纵向对了就成立，右侧留白也比压在正文上更不挡字。
     *
     *  量不出来（无布局引擎、元素缺失、宽高为 0）就退到右上角：按钮依然可见可用，只是不再
     *  贴着那一行。**本函数不负责显隐**，调用方已经把它放出来了。 */
    function mrPlacePlaceholderAction(area, action, info) {
        const toCorner = function () {
            action.style.top = '6px';
            action.style.left = '';
            action.style.right = '18px';
        };
        const wrap = area ? area.parentNode : null;
        if (!wrap || !info || typeof wrap.appendChild !== 'function'
            || typeof document.createElement !== 'function') {
            return toCorner();
        }
        let mirror = wrap.__mrPlaceholderMirror;
        if (!mirror) {
            mirror = document.createElement('div');
            mirror.style.position = 'absolute';
            mirror.style.top = '0';
            mirror.style.left = '0';
            mirror.style.visibility = 'hidden';
            mirror.style.pointerEvents = 'none';
            mirror.style.overflow = 'hidden';
            mirror.style.whiteSpace = 'pre-wrap';
            mirror.style.wordWrap = 'break-word';
            wrap.appendChild(mirror);
            wrap.__mrPlaceholderMirror = mirror;
        }
        const styles = (typeof window.getComputedStyle === 'function') ? window.getComputedStyle(area) : null;
        if (styles && styles.fontSize) {
            ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'lineHeight', 'textIndent',
                'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
                'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth', 'boxSizing']
                .forEach(function (key) { mirror.style[key] = styles[key]; });
        }
        // 宽度要跟 textarea 的**外框**一致（含 padding/border），换行点才会落在同一列。
        mirror.style.width = (area.offsetWidth || 0) + 'px';
        mirror.style.height = 'auto';
        const text = String(area.value || '');
        mirror.textContent = text.slice(0, info.start);
        const mark = document.createElement('span');
        mark.textContent = text.slice(info.start, info.end);
        mirror.appendChild(mark);

        if (typeof mark.getBoundingClientRect !== 'function' || typeof area.getBoundingClientRect !== 'function'
            || typeof action.getBoundingClientRect !== 'function') {
            return toCorner();
        }
        const markRect = mark.getBoundingClientRect();
        const areaRect = area.getBoundingClientRect();
        const chipRect = action.getBoundingClientRect();
        if (!markRect.height || !areaRect.width || !chipRect.width) return toCorner();
        // 镜像与 textarea 共用包裹层的原点，两者左边缘都在 0 上，所以直接拿「行相对
        // textarea 顶部的偏移」就够，不必再换算绝对坐标。
        const lineTop = markRect.top - areaRect.top;
        const top = Math.max(2, Math.round(lineTop + (markRect.height - chipRect.height) / 2));
        const gap = 6;
        let left = Math.round(areaRect.width - chipRect.width - gap);
        const afterToken = Math.round(markRect.left - areaRect.left + markRect.width + gap);
        // 令牌后面就有空位（正文行通常没写满）时贴着令牌放，指代最清楚；
        // 否则贴右缘 —— 两种情况都不会越界（afterToken 严格小于贴右缘的位置才会采用）。
        if (afterToken < left) left = afterToken;
        action.style.right = '';
        action.style.top = top + 'px';
        action.style.left = Math.max(2, left) + 'px';
    }

    /** 光标一动就重算浮层：落在占位符上 → 出现并摆位；不在 → 全收起。 */
    function mrSyncPlaceholderAction(areaId) {
        const conf = MR_FIGURE_ACTION_AREAS[areaId];
        if (!conf) return;
        // 同一时刻只有一个人在编辑：先把两个都收掉，再决定要不要放出自己这一个。
        // 否则「在题面里留一个、在解析里再留一个」，两个按钮同时挂着，看不出属于谁。
        mrHidePlaceholderActions();
        const area = el(areaId);
        const action = el(conf.actionId);
        if (!area || !action) return;
        const info = mrPlaceholderAt(area.value, area.selectionStart);
        if (!info) return;
        action.innerHTML = mrPlaceholderActionHtml(conf.target, info);
        action.classList.remove('hidden');
        mrPlacePlaceholderAction(area, action, info);
    }

    /** 收起一个输入框的补图浮层。 */
    function mrHidePlaceholderAction(areaId) {
        const conf = MR_FIGURE_ACTION_AREAS[areaId];
        if (!conf) return;
        const action = el(conf.actionId);
        if (action) action.classList.add('hidden');
    }

    /** 两个输入框的补图浮层一起收起（换题、打开框选弹窗时用）。 */
    function mrHidePlaceholderActions() {
        Object.keys(MR_FIGURE_ACTION_AREAS).forEach(function (areaId) {
            mrHidePlaceholderAction(areaId);
        });
    }

    /** 「清除残留占位符」只在当前这道题真的还有占位符时露出来。 */
    function updateMrPlaceholderButton() {
        const btn = el('mrClearPlaceholderBtn');
        if (!btn) return;
        const content = el('mrContent');
        const answer = el('mrAnswer');
        const total = mrPlaceholderCount(content && content.value) + mrPlaceholderCount(answer && answer.value);
        btn.style.display = total ? '' : 'none';
    }

    /** 把题面/解析里没打算补图的占位符一次清掉 —— 不补的图不该在学生讲义上留一行标记。 */
    function clearMistakePlaceholders() {
        const record = reviewDraft.recordId ? findRecord(reviewDraft.recordId) : null;
        if (!record) { toast('先在左侧选一道题', 'info'); return; }
        const content = String(el('mrContent').value || '');
        const answer = String(el('mrAnswer').value || '');
        const nextContent = mrTidyBlankLines(content.replace(MR_PLACEHOLDER_RE, ''));
        const nextAnswer = mrTidyBlankLines(answer.replace(MR_PLACEHOLDER_RE, ''));
        if (nextContent === content && nextAnswer === answer) {
            toast('这道题没有待补的占位符', 'info');
            return;
        }
        if (!window.confirm('清掉这道题里所有「[插图待补: 图N]」标记？\n\n已经补成图片的位置不受影响。')) return;
        patchRecord(record.id, { content: nextContent, answer_markdown: nextAnswer }).then(function () {
            el('mrContent').value = nextContent;
            el('mrAnswer').value = nextAnswer;
            reviewDraft.syncedContent = nextContent;
            reviewDraft.syncedAnswer = nextAnswer;
            toast('已清掉残留的占位符');
            renderReviewList();
            renderReviewPreview();
            updateReviewButtons();
        });
    }

    /** 摘掉占位符后常会留下三四行空行，收拢一下，别在讲义里留一片空白。 */
    function mrTidyBlankLines(text) {
        return String(text || '')
            .replace(/[ \t]+\n/g, '\n')
            .replace(/\n{3,}/g, '\n\n')
            .replace(/\s+$/, '');
    }

    /* ===================== 原卷框选补图 =====================
       2026-09-15 需求：补图必须在**原卷页面上框选**，而不是从磁盘挑一张已有图片。
       原卷页面图与块坐标本来就随批次详情下发（state.pages[].url / blocks[]），所以这里
       不新增取图接口，直接复用与切块同一套 0–1 归一化坐标。
       一个弹窗服务两个目标：figure_images（本题配图）与 answer_images（解析截图）。 */

    const MR_CROP_TARGET_INFO = {
        figure_images: {
            title: '原卷框选 · 补本题配图',
            badge: '补入位置：本题配图',
            empty: '先在图上拖拽框选题目里的图形区域'
        },
        answer_images: {
            title: '原卷框选 · 补解析截图',
            badge: '补入位置：解析截图',
            empty: '先在图上拖拽框选解答/解析所在的区域'
        }
    };

    // 当前一次框选的会话状态；弹窗没开时为 null。
    let mrCrop = null;
    let mrCropListenersBound = false;
    const MR_CROP_MIN_PX = 8;
    // 角命中半径(px)：四角圆点已取消（2026-09-15），改用它做纯坐标判定，
    // 与题库侧 hitCropHandle 的 CROP_HANDLE_HIT 保持一致。
    const MR_CROP_HANDLE_HIT = 10;

    function mrCropInfo(target) {
        return MR_CROP_TARGET_INFO[target] || MR_CROP_TARGET_INFO.figure_images;
    }

    /** 正文里图片该插在哪：目标字段 + 光标位。点按钮那一刻 textarea 已经失焦，但
     *  ``selectionStart`` 会留着，所以这里读得到用户真正的光标位置。 */
    function mrCropInsertPoint(target, placeholderIndex) {
        const isAnswer = target === 'answer_images';
        const areaId = isAnswer ? 'mrAnswer' : 'mrContent';
        const tracked = (reviewDraft.cursor && reviewDraft.cursor.areaId === areaId)
            ? reviewDraft.cursor.pos : null;
        return {
            field: isAnswer ? 'answer_markdown' : 'content',
            areaId: areaId,
            cursor: tracked,
            placeholderIndex: (typeof placeholderIndex === 'number' && placeholderIndex >= 0)
                ? placeholderIndex : null
        };
    }

    /** 源码区浮层入口（2026-09-18 起从预览搬过来）：带着序号回到框选弹窗，补完就地替换那一个。 */
    function openMistakeCropModalFromPlaceholder(target, placeholderIndex) {
        openMistakeCropModal(target, placeholderIndex);
    }

    /** HTML 属性转义。配图 URL 来自后端（自己拼的静态路径），仍然按不可信处理。 */
    function mrCropAttr(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function mrCropDisplaySize() {
        const img = el('mrCropImage');
        if (!img) return { w: 0, h: 0 };
        return { w: img.clientWidth || 0, h: img.clientHeight || 0 };
    }

    function mrCropPage() {
        if (!mrCrop) return null;
        return state.pages[mrCrop.pageIndex] || null;
    }

    /** 本题块在页面上的像素位置（用于「本题范围」参考框与打开时定位）。 */
    function mrCropBlockPixels() {
        if (!mrCrop || !mrCrop.blockRect) return null;
        const box = mrCropDisplaySize();
        if (!box.w || !box.h) return null;
        const rect = mrCrop.blockRect;
        return {
            left: Math.round(rect[0] * box.w),
            top: Math.round(rect[1] * box.h),
            width: Math.max(1, Math.round((rect[2] - rect[0]) * box.w)),
            height: Math.max(1, Math.round((rect[3] - rect[1]) * box.h))
        };
    }

    /** 打开框选弹窗。target：figure_images / answer_images。
     *
     *  ``placeholderIndex`` = 从预览里点了第几个（0 起）占位符芯片进来的。带它就表示
     *  这次补的图**就地替换那个占位符**；不带（工具条进来）则插到 textarea 的光标处。
     */
    function openMistakeCropModal(target, placeholderIndex) {
        const recordId = reviewDraft.recordId;
        if (!recordId) { toast('先在左侧选一道题', 'info'); return; }
        const record = findRecord(recordId);
        if (!record) return;
        if (!state.pages.length) { toast('本批次还没有页面图，请先切题', 'error'); return; }
        const modal = el('mrCropModal');
        if (!modal) return;

        flushReviewSave();
        // 弹窗一开就把源码区的浮层收掉：它此刻已经没用了，留着只会盖在弹窗下面。
        mrHidePlaceholderActions();
        const startIndex = Math.max(0, state.pages.indexOf(pageByNo(record.page_no)));
        const info = mrCropInfo(target);
        mrCrop = {
            recordId: recordId,
            target: target,
            pageIndex: startIndex,
            zoom: 1,
            natural: { w: 0, h: 0 },
            blockRect: rectOfRecord(record),
            rect: null,
            drag: null,
            insert: mrCropInsertPoint(target, placeholderIndex)
        };

        const title = el('mrCropTitleText');
        if (title) title.textContent = info.title;
        const badge = el('mrCropTargetBadgeText');
        if (badge) badge.textContent = info.badge;
        const hint = el('mrCropHintText');
        if (hint) {
            const landing = mrCrop.insert.placeholderIndex !== null
                ? '确认后把源码里那个「插图待补」占位符就地换成这张图'
                : '确认后插到你光标停的位置（先点一下要插图的地方）';
            hint.innerHTML = '已自动跳到本题所在的第 <b class="text-brand-300">' + record.page_no +
                '</b> 页并滚到本题位置；虚线框是本题在识别时的取图范围。' + landing + '。';
        }

        clearMrCropSelection();
        renderMrCropThumbs();
        bindMrCropListeners();
        loadMrCropPage(startIndex);

        modal.classList.remove('hidden');
        if (window.MathBankModal && typeof window.MathBankModal.open === 'function') {
            window.MathBankModal.open(modal, { onEscape: handleMrCropEscape });
        }
        setTimeout(function () {
            modal.classList.remove('opacity-0');
            const box = modal.querySelector('.glass-modal');
            if (box) { box.classList.remove('scale-95'); box.classList.add('scale-100'); }
        }, 30);
    }

    function closeMistakeCropModal() {
        const modal = el('mrCropModal');
        mrCrop = null;
        if (!modal) return;
        if (window.MathBankModal && typeof window.MathBankModal.close === 'function') {
            window.MathBankModal.close(modal);
        }
        modal.classList.add('opacity-0');
        const box = modal.querySelector('.glass-modal');
        if (box) { box.classList.remove('scale-100'); box.classList.add('scale-95'); }
        setTimeout(function () { modal.classList.add('hidden'); }, 200);
    }

    /** ESC 分级：有框选先清框选，没框选才关弹窗（与导入页的手动截图一致）。 */
    function handleMrCropEscape() {
        if (mrCrop && mrCrop.rect) { clearMrCropSelection(); return; }
        closeMistakeCropModal();
    }

    function renderMrCropThumbs() {
        const box = el('mrCropThumbs');
        if (!box || !mrCrop) return;
        const record = findRecord(mrCrop.recordId);
        box.innerHTML = state.pages.map(function (page, index) {
            const isCurrent = !!record && record.page_no === page.page_no;
            const active = index === mrCrop.pageIndex;
            return '<button type="button" onclick="mrCropGotoPage(' + index + ')" class="block w-full text-left rounded-lg overflow-hidden border ' +
                (active ? 'border-brand-500' : 'border-slate-200') + ' bg-white">' +
                '<img src="' + mrCropAttr(page.url) + '" alt="第 ' + page.page_no + ' 页" class="w-full block">' +
                '<span class="block px-1.5 py-1 text-[10px] font-bold ' + (isCurrent ? 'text-brand-600' : 'text-slate-400') + '">第 ' +
                page.page_no + ' 页' + (isCurrent ? ' · 本题' : '') + '</span>' +
                '</button>';
        }).join('');
    }

    function mrCropGotoPage(index) {
        if (!mrCrop) return;
        if (index < 0 || index >= state.pages.length) return;
        loadMrCropPage(index);
    }

    function mrCropStepPage(step) {
        if (!mrCrop) return;
        mrCropGotoPage(mrCrop.pageIndex + step);
    }

    function loadMrCropPage(index) {
        const page = state.pages[index];
        const img = el('mrCropImage');
        if (!page || !img || !mrCrop) return;
        mrCrop.pageIndex = index;
        mrCrop.zoom = 1;
        clearMrCropSelection();
        img.onload = function () {
            if (!mrCrop) return;
            mrCrop.natural = { w: img.naturalWidth || 1, h: img.naturalHeight || 1 };
            applyMrCropFit();
            scrollMrCropToBlock();
        };
        // 用 data-loaded 记「当前这张图是谁」：img.src 会被浏览器补成绝对地址，
        // 拿它跟相对路径比永远不等；同页重开时 onload 也不会再触发，要主动补一次。
        if (img.getAttribute('data-loaded') !== page.url) {
            img.setAttribute('data-loaded', page.url);
            img.src = page.url;
        } else if (img.complete && typeof img.onload === 'function') {
            img.onload();
        }
        const indicator = el('mrCropPageIndicator');
        if (indicator) indicator.textContent = '第 ' + (index + 1) + ' / ' + state.pages.length + ' 页';
        renderMrCropThumbs();
    }

    /** 自适应：整页塞进可视区，再乘用户缩放倍数。 */
    function applyMrCropFit() {
        const wrapper = el('mrCropWrapper');
        const img = el('mrCropImage');
        if (!wrapper || !img || !mrCrop || !mrCrop.natural.w) return;
        const availW = Math.max(160, (wrapper.clientWidth || 640) - 48);
        const availH = Math.max(160, (wrapper.clientHeight || 480) - 48);
        const fit = Math.min(availW / mrCrop.natural.w, availH / mrCrop.natural.h);
        const width = Math.max(80, Math.round(mrCrop.natural.w * fit * mrCrop.zoom));
        img.style.width = width + 'px';
        img.style.height = 'auto';
        const zoomText = el('mrCropZoomText');
        if (zoomText) zoomText.textContent = Math.round(mrCrop.zoom * 100) + '%';
        paintMrCropBlockRect();
        paintMrCropSelection();
    }

    function scrollMrCropToBlock() {
        const wrapper = el('mrCropWrapper');
        const rect = mrCropBlockPixels();
        if (!wrapper || !rect) return;
        wrapper.scrollTop = Math.max(0, rect.top - 48);
        wrapper.scrollLeft = Math.max(0, rect.left - 48);
    }

    function paintMrCropBlockRect() {
        const node = el('mrCropBlockRect');
        if (!node) return;
        const rect = mrCropBlockPixels();
        if (!rect) { node.classList.add('hidden'); return; }
        node.classList.remove('hidden');
        node.style.left = rect.left + 'px';
        node.style.top = rect.top + 'px';
        node.style.width = rect.width + 'px';
        node.style.height = rect.height + 'px';
    }

    /** 画框选矩形。刻意不显示「761 × 164 px」这类尺寸数字：老师判断的是图形框得对不对，
     *  像素数只是干扰（2026-09-15 用户明确要求去掉）。 */
    function paintMrCropSelection() {
        const node = el('mrCropSelectRect');
        if (!node) return;
        const rect = mrCrop && mrCrop.rect;
        if (!rect || rect.w < 1 || rect.h < 1) {
            node.classList.add('hidden');
            updateMrCropButtons();
            return;
        }
        node.classList.remove('hidden');
        node.style.left = Math.round(rect.x) + 'px';
        node.style.top = Math.round(rect.y) + 'px';
        node.style.width = Math.round(rect.w) + 'px';
        node.style.height = Math.round(rect.h) + 'px';
        updateMrCropButtons();
    }

    function updateMrCropButtons() {
        const ready = !!(mrCrop && mrCrop.rect &&
            mrCrop.rect.w >= MR_CROP_MIN_PX && mrCrop.rect.h >= MR_CROP_MIN_PX);
        ['mrCropClearBtn', 'mrCropConfirmBtn'].forEach(function (id) {
            const node = el(id);
            if (!node) return;
            node.disabled = !ready;
            node.classList.toggle('opacity-40', !ready);
            node.classList.toggle('pointer-events-none', !ready);
        });
    }

    function clearMrCropSelection() {
        if (mrCrop) { mrCrop.rect = null; mrCrop.drag = null; }
        paintMrCropSelection();
    }

    function mrCropPoint(event) {
        const img = el('mrCropImage');
        if (!img) return null;
        const box = img.getBoundingClientRect();
        if (!box || !box.width || !box.height) return null;
        return {
            x: Math.max(0, Math.min(box.width, event.clientX - box.left)),
            y: Math.max(0, Math.min(box.height, event.clientY - box.top)),
            w: box.width,
            h: box.height
        };
    }

    function bindMrCropListeners() {
        if (mrCropListenersBound) return;
        const box = el('mrCropImageBox');
        if (!box) return;
        mrCropListenersBound = true;
        box.addEventListener('mousedown', onMrCropMouseDown);
        box.addEventListener('mousemove', onMrCropHover);
        document.addEventListener('mousemove', onMrCropMouseMove);
        document.addEventListener('mouseup', onMrCropMouseUp);
        window.addEventListener('resize', function () { if (mrCrop) applyMrCropFit(); });
    }

    /** 命中某个角？返回 'nw'|'ne'|'sw'|'se'，否则 null。
     *
     *  四角圆点取消后（2026-09-15），缩放全靠这个坐标判定 —— 页面图上不留任何
     *  可见或隐藏的手柄元素，鼠标靠近角只换光标。与题库侧 hitCropHandle 同源。
     */
    function mrCropHitHandle(point) {
        const rect = mrCrop && mrCrop.rect;
        if (!rect || rect.w <= 0 || rect.h <= 0) return null;
        const corners = {
            nw: [rect.x, rect.y],
            ne: [rect.x + rect.w, rect.y],
            sw: [rect.x, rect.y + rect.h],
            se: [rect.x + rect.w, rect.y + rect.h]
        };
        const keys = Object.keys(corners);
        for (let k = 0; k < keys.length; k += 1) {
            const c = corners[keys[k]];
            if (Math.abs(point.x - c[0]) <= MR_CROP_HANDLE_HIT &&
                Math.abs(point.y - c[1]) <= MR_CROP_HANDLE_HIT) {
                return keys[k];
            }
        }
        return null;
    }

    function mrCropHitInside(point) {
        const rect = mrCrop && mrCrop.rect;
        if (!rect) return false;
        return point.x >= rect.x && point.x <= rect.x + rect.w &&
            point.y >= rect.y && point.y <= rect.y + rect.h;
    }

    /** 悬停光标反馈。四角圆点取消后，光标是「拖到角上可以缩放」的唯一提示。 */
    function onMrCropHover(event) {
        if (!mrCrop || mrCrop.drag) return;
        const box = el('mrCropImageBox');
        if (!box) return;
        const point = mrCropPoint(event);
        if (!point) return;
        const handle = mrCropHitHandle(point);
        if (handle) {
            box.style.cursor = (handle === 'nw' || handle === 'se') ? 'nwse-resize' : 'nesw-resize';
        } else if (mrCropHitInside(point)) {
            box.style.cursor = 'move';
        } else {
            box.style.cursor = 'crosshair';
        }
    }

    function onMrCropMouseDown(event) {
        if (!mrCrop) return;
        const point = mrCropPoint(event);
        if (!point) return;
        event.preventDefault();
        // 顺序要紧：先判角（缩放）→ 再判框内（整体平移）→ 最后才新建框。
        const handle = mrCropHitHandle(point);
        if (handle) {
            mrCrop.drag = { mode: 'resize', handle: handle, origin: Object.assign({}, mrCrop.rect) };
        } else if (mrCropHitInside(point)) {
            mrCrop.drag = { mode: 'move', start: point, origin: Object.assign({}, mrCrop.rect) };
        } else {
            mrCrop.rect = { x: point.x, y: point.y, w: 0, h: 0 };
            mrCrop.drag = { mode: 'draw', anchorX: point.x, anchorY: point.y };
            paintMrCropSelection();
        }
    }

    function onMrCropMouseMove(event) {
        if (!mrCrop || !mrCrop.drag) return;
        const point = mrCropPoint(event);
        if (!point) return;
        event.preventDefault();
        const drag = mrCrop.drag;
        if (drag.mode === 'draw') {
            mrCrop.rect = {
                x: Math.min(drag.anchorX, point.x),
                y: Math.min(drag.anchorY, point.y),
                w: Math.abs(point.x - drag.anchorX),
                h: Math.abs(point.y - drag.anchorY)
            };
        } else if (drag.mode === 'move') {
            mrCrop.rect = {
                x: Math.max(0, Math.min(point.w - drag.origin.w, drag.origin.x + (point.x - drag.start.x))),
                y: Math.max(0, Math.min(point.h - drag.origin.h, drag.origin.y + (point.y - drag.start.y))),
                w: drag.origin.w,
                h: drag.origin.h
            };
        } else if (drag.mode === 'resize') {
            const o = drag.origin;
            let left = o.x;
            let top = o.y;
            let right = o.x + o.w;
            let bottom = o.y + o.h;
            if (drag.handle.indexOf('w') >= 0) left = Math.min(point.x, right - MR_CROP_MIN_PX);
            if (drag.handle.indexOf('e') >= 0) right = Math.max(point.x, left + MR_CROP_MIN_PX);
            if (drag.handle.indexOf('n') >= 0) top = Math.min(point.y, bottom - MR_CROP_MIN_PX);
            if (drag.handle.indexOf('s') >= 0) bottom = Math.max(point.y, top + MR_CROP_MIN_PX);
            left = Math.max(0, left);
            top = Math.max(0, top);
            right = Math.min(point.w, right);
            bottom = Math.min(point.h, bottom);
            mrCrop.rect = { x: left, y: top, w: right - left, h: bottom - top };
        }
        paintMrCropSelection();
    }

    function onMrCropMouseUp() {
        if (!mrCrop || !mrCrop.drag) return;
        const mode = mrCrop.drag.mode;
        mrCrop.drag = null;
        // 拖出来的框太小＝手滑点了一下，当作没框（否则会留下一个 2px 的框让人误会）。
        if (mode === 'draw' && (!mrCrop.rect ||
            mrCrop.rect.w < MR_CROP_MIN_PX || mrCrop.rect.h < MR_CROP_MIN_PX)) {
            mrCrop.rect = null;
        }
        paintMrCropSelection();
    }

    function zoomMrCrop(step) {
        if (!mrCrop) return;
        mrCrop.zoom = Math.max(0.4, Math.min(4, Math.round((mrCrop.zoom + step) * 100) / 100));
        applyMrCropFit();
    }

    function zoomMrCropIn() { zoomMrCrop(0.2); }

    function zoomMrCropOut() { zoomMrCrop(-0.2); }

    function resetMrCropZoom() {
        if (!mrCrop) return;
        mrCrop.zoom = 1;
        applyMrCropFit();
    }

    /**
     * 确认：把归一化框选坐标交给后端裁图，再把图**写进正文**。
     *
     * 位置由正文决定，不再往记录尾部追加：
     *   - 从占位符芯片进来的 → 就地把那个 `[插图待补: 图N]` 换成 `![插图](url)`；
     *   - 从工具条进来的 → 插到 textarea 里光标停的位置。
     * 另外把「原本配给这个占位符的那张数组图」摘掉：它已经内联进正文了，留在数组里
     * 会跟下一个占位符错配，导出时还会把同一张图多印一遍。
     */
    function submitMrCrop() {
        const record = mrCrop ? findRecord(mrCrop.recordId) : null;
        const page = mrCropPage();
        const rect = mrCrop ? mrCrop.rect : null;
        const insert = mrCrop ? mrCrop.insert : null;
        if (!mrCrop || !record || !page || !rect || !insert) {
            toast('先在图上拖拽框选区域', 'info');
            return;
        }
        const box = mrCropDisplaySize();
        if (!box.w || !box.h) return;
        const body = {
            xmin: rect.x / box.w,
            ymin: rect.y / box.h,
            xmax: (rect.x + rect.w) / box.w,
            ymax: (rect.y + rect.h) / box.h
        };
        const target = mrCrop.target;
        const btn = el('mrCropConfirmBtn');
        // 确认按钮的可用性归 updateMrCropButtons 管（拖出框才可点）。失败时框还在，
        // 调它一下就把按钮交回「有框 → 可点」，用户能直接重试。以前写的是
        // 「在 then 里 btn.disabled = false」，而 fetch 在网络中断时直接 reject、
        // then 根本不执行，确认按钮就永久停在禁用态，只能刷新页面重来。
        const restoreCropBtn = function () { updateMrCropButtons(); };
        if (btn) btn.disabled = true;
        api('/api/mistakes/batches/' + state.batch.id + '/pages/' + page.page_no + '/crop-figure', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (res) {
            if (!res.ok || !res.data || !res.data.image_path) {
                toast((res.data && res.data.message) || '截取失败', 'error');
                if (btn) btn.disabled = false;
                return null;
            }
            const url = res.data.image_path;
            const area = el(insert.areaId);
            // 以 textarea 里的内容为准：用户可能刚改过题面还没落盘，而预览用的是记录里的旧文本。
            const source = area ? String(area.value || '') : String(record[insert.field] || '');
            const markdown = mrImageMarkdown(url);
            let next = null;
            if (insert.placeholderIndex !== null) {
                next = mrReplacePlaceholder(source, insert.placeholderIndex, markdown);
            }
            if (next === null) {
                // 占位符被手删过、或本来就是工具条进来的 → 落到光标处；没记过光标就落末尾。
                next = mrInsertMarkdownImage(source, insert.cursor, markdown).text;
            }
            const payload = {};
            payload[insert.field] = next;
            if (target === 'answer_images') payload.answer_source = 'manual';
            return patchRecord(record.id, payload).then(function (saved) {
                // patchRecord 落库失败时返回 null（并已自行提示）。截图上传成功不等于
                // 图进了题面：这时不能改编辑区、不能关弹窗，否则界面上看着插好了、
                // 库里其实没有，用户不会再回来补第二次。
                if (!saved) {
                    restoreCropBtn();
                    return null;
                }
                if (area) area.value = next;
                if (record.id === reviewDraft.recordId) {
                    if (insert.field === 'content') reviewDraft.syncedContent = next;
                    else reviewDraft.syncedAnswer = next;
                }
                toast(target === 'figure_images' ? '已把配图插到题面里' : '已把解析截图插到解析里');
                closeMistakeCropModal();
                renderReviewList();
                renderReviewPreview();
                updateReviewButtons();
                return saved;
            });
        }).catch(function (error) {
            restoreCropBtn();
            toast('插入配图失败：' + ((error && error.message) || error || '请求未送达') + '（可重试）', 'error');
        });
    }

    /* 说明：这里刻意**不**去动 figure_images / answer_images。
       配对规则是「数组第 k 张 ↔ 第 k 个还没填的占位符」，而 a chip 只在数组配不出图时
       才出现（能配出图的那个占位符直接出图、不是芯片）—— 所以芯片的序号 k 必然 >=
       数组长度，压根没有「配对项」可摘。真去摘反而会把下一张图错配给上一个占位符。 */

    /** 审校页移除一张已补的图（补错了要能撤）。 */
    function removeMistakeFigure(recordId, index, field) {
        const record = findRecord(recordId);
        if (!record) return;
        const source = (field === 'answer_images' ? record.answer_images : record.figure_images) || [];
        if (index < 0 || index >= source.length) return;
        const next = source.slice();
        next.splice(index, 1);
        const body = {};
        body[field] = next;
        patchRecord(recordId, body).then(function () {
            toast('已移除该图');
            renderReviewList();
            renderReviewPreview();
        });
    }

    /** 预览里的配图条：补进去的图得看得见，也能删。 */
    function reviewFigureStripHtml(record) {
        const figures = (record.figure_images || []);
        if (!figures.length) return '';
        const cells = figures.map(function (url, index) {
            return '<span class="relative inline-block">' +
                '<img src="' + mrCropAttr(url) + '" alt="本题配图" class="h-16 rounded-lg border border-slate-200 dark:border-slate-700">' +
                '<button type="button" title="移除这张配图" onclick="removeMistakeFigure(' + record.id + ',' + index + ',&#39;figure_images&#39;)" ' +
                'class="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-600 text-slate-500 text-[10px] leading-none flex items-center justify-center">' +
                '<i class="fa-solid fa-xmark text-[9px]"></i></button></span>';
        }).join('');
        return '<div class="mt-4 pt-3 border-t border-dashed border-slate-200 dark:border-slate-700">' +
            '<div class="text-[10px] font-bold text-slate-400 mb-1.5">本题配图（' + figures.length + '）</div>' +
            '<div class="flex flex-wrap gap-2">' + cells + '</div></div>';
    }

    /** 「生成错题本」：门禁 → 存盘 → 只把本次收录的题入库 → 灌试题篮 → 切组卷。 */
    function generateReviewHandout() {
        const records = reviewRecords();
        if (!records.length) { toast('本次没有收录任何题目', 'error'); return; }
        // 识别还没跑完就入库，后续落库的识别结果会改写已经入库的题 —— 题面与题库里
        // 那份对不上。宁可让用户等几秒。
        if (state.recognizeLive.active) {
            toast('识别还在进行中，等它跑完再入库 —— 否则刚入库的题会被后续识别结果改写。', 'error');
            return;
        }
        const empty = records.filter(function (record) { return !String(record.content || '').trim(); });
        if (empty.length) {
            toast('有 ' + empty.length + ' 道题面为空，先识别或手动补写：' +
                empty.map(function (record) { return record.question_no || (record.page_no + ' 页'); }).join('、'), 'error');
            return;
        }
        const subjectLabel = SUBJECT_LABELS[state.batch.subject || 'math'] || '数学';
        if (!window.confirm('将把本次收录的 ' + records.length + ' 道题入库（' + subjectLabel +
            '），并自动放进组卷试题篮、切到组卷工作台。\n\n继续？')) return;
        Promise.resolve(flushReviewSave()).then(function () {
            const ids = records.map(function (record) { return record.id; });
            closeMistakeReview();
            importMistakeBatch(ids);
        });
    }

    // ---------------------------------------------------------------- 导出

    function openMistakeExportPanel() {
        const panel = el('mistakeExportPanel');
        if (!panel) return;
        panel.classList.remove('hidden');
        panel.classList.add('flex');
        panel.setAttribute('aria-hidden', 'false');
        const result = el('mistakeExportResult');
        if (result) result.classList.add('hidden');
    }

    function closeMistakeExportPanel() {
        const panel = el('mistakeExportPanel');
        if (!panel) return;
        panel.classList.add('hidden');
        panel.classList.remove('flex');
        panel.setAttribute('aria-hidden', 'true');
    }

    function exportMistakeHandout() {
        if (!state.batch) return;
        const button = el('mistakeExportBtn');
        if (button) { button.disabled = true; button.textContent = '正在编译…'; }
        const payload = {
            solution_space_cm: parseFloat(el('mistakeSpaceInput').value || '6'),
            include_reason: el('mistakeIncludeReasonInput').checked,
            include_figures: el('mistakeIncludeFiguresInput').checked,
            answer_mode: el('mistakeAnswerModeInput').value,
            font_size: el('mistakeFontSizeInput').value,
            show_original_number: el('mistakeShowNumberInput').checked
        };
        api('/api/mistakes/batches/' + state.batch.id + '/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(function (res) {
            if (button) { button.disabled = false; button.textContent = '生成 PDF'; }
            const box = el('mistakeExportResult');
            if (!res.ok) {
                if (box) {
                    box.classList.remove('hidden');
                    box.className = 'text-[11px] rounded-lg px-3 py-2 bg-red-50 text-red-700 border border-red-200';
                    box.textContent = res.data.message || '生成失败';
                }
                return;
            }
            state.lastExport = res.data;
            if (box) {
                box.classList.remove('hidden');
                box.className = 'text-[11px] rounded-lg px-3 py-2 bg-emerald-50 text-emerald-700 border border-emerald-200';
                box.innerHTML = '已收录 ' + res.data.included_count + ' 题，含解析 ' + res.data.answer_count + ' 题。' +
                    ((res.data.blocked_ai_answers || []).length
                        ? '<br><b>有 ' + res.data.blocked_ai_answers.length + ' 道题的 AI 解析尚未人工核对，未进 PDF：</b>原卷 ' + res.data.blocked_ai_answers.join('、')
                        : '') +
                    '<br><a class="underline font-semibold" href="' + esc(res.data.pdf_url) + '" target="_blank">在新窗口打开 / 下载 PDF</a>';
            }
            toast('错题本已生成（' + res.data.included_count + ' 题）');
            refreshMistakeDetail();
        }).catch(function (error) {
            // 编译 PDF 要几十秒，用户最可能在这期间关掉服务或断网；没有拒绝分支的话
            // 按钮会一直停在「正在编译…」，既看不出失败也没法重试。
            if (button) { button.disabled = false; button.textContent = '生成 PDF'; }
            const box = el('mistakeExportResult');
            if (box) {
                box.classList.remove('hidden');
                box.className = 'text-[11px] rounded-lg px-3 py-2 bg-red-50 text-red-700 border border-red-200';
                box.textContent = '生成失败：' + ((error && error.message) || error || '请求未送达') + '（可重试）';
            }
            toast('生成 PDF 失败，请重试', 'error');
        });
    }

    // ---------------------------------------------------------------- 统计与快捷键

    function renderStats() {
        const box = el('mistakeStats');
        if (!box || !state.batch) return;
        const batch = state.batch;
        const parts = [
            '<span>共 <b>' + (batch.total || 0) + '</b> 题</span>',
            '<span class="text-emerald-600">对 ' + (batch.correct || 0) + '</span>',
            '<span class="text-red-600">错 ' + (batch.incorrect || 0) + '</span>',
            '<span class="text-slate-500">未批 ' + (batch.unknown || 0) + '</span>',
            '<span>已识别 ' + (batch.recognized || 0) + '</span>',
            '<span>已收录 <b>' + (batch.included || 0) + '</b></span>',
            '<span>已入库 ' + (batch.imported || 0) + '</span>'
        ];
        if (batch.failed) parts.push('<span class="text-red-600">识别失败 ' + batch.failed + '</span>');
        box.innerHTML = parts.join('<span class="text-slate-300">·</span>');
        updateRecognizeButton();
    }

    function bindKeyboard() {
        document.addEventListener('keydown', function (event) {
            const section = el('mistakeWorkspaceSection');
            if (!section || section.classList.contains('hidden')) return;
            const detail = el('mistakeDetailView');
            if (!detail || detail.classList.contains('hidden')) return;
            const tag = (event.target.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
            // 合并的快捷键与「选中某题」无关（可能一块都没选中，只勾了两块），
            // 因此必须放在 selectedRecordId 的门禁之前。
            if (event.key === 'Enter') {
                if (state.mergePick.length >= 2) { requestMerge(); event.preventDefault(); }
                else if (state.mergePick.length === 1) { toast('再选一块：⌘/Ctrl + 点击另一块', 'error'); event.preventDefault(); }
                return;
            }
            if (event.key === 'Escape') {
                if (state.mergePick.length) { clearMergePick(); event.preventDefault(); }
                return;
            }
            // ⌘Z / Ctrl+Z 撤销上一步（删除、合并、拆分…）。放在选中门禁之前：用户
            // 删完一块，那一题连选中都没了，不该因此撤销不了。
            if ((event.metaKey || event.ctrlKey) && (event.key === 'z' || event.key === 'Z')) {
                undoLastAction();
                event.preventDefault();
                return;
            }
            if (!state.selectedRecordId) return;
            // 3 保留给「清空判定」：卡片上已经没有未批按钮了，光靠点已选中的那个按钮
            // 取消是唯一入口，键盘上得留一条等价的路。
            if (event.key === '1') { setMistakeGrad(state.selectedRecordId, 'correct'); event.preventDefault(); }
            else if (event.key === '2') { setMistakeGrad(state.selectedRecordId, 'incorrect'); event.preventDefault(); }
            else if (event.key === '3') { setMistakeGrad(state.selectedRecordId, 'unknown'); event.preventDefault(); }
            else if (event.key === 'j' || event.key === 'J') { moveSelection(1); event.preventDefault(); }
            else if (event.key === 'k' || event.key === 'K') { moveSelection(-1); event.preventDefault(); }
        });
    }

    function moveSelection(delta) {
        const records = visibleRecords();
        if (!records.length) return;
        const index = records.findIndex(function (record) { return record.id === state.selectedRecordId; });
        const next = Math.min(records.length - 1, Math.max(0, (index < 0 ? 0 : index + delta)));
        selectRecord(records[next].id);
    }

    // ---------------------------------------------------------------- 工作台切换

    function showMistakeWorkspace() {
        const bankSec = el('bankWorkspaceSection');
        const paperSec = el('paperWorkspaceSection');
        const mistakeSec = el('mistakeWorkspaceSection');
        const topFilterBar = el('bankTopFilterBar');
        const toggleSidebarBtn = el('toggleSidebarBtn');
        if (bankSec) bankSec.classList.add('hidden');
        if (paperSec) paperSec.classList.add('hidden');
        if (topFilterBar) topFilterBar.classList.add('hidden');
        if (toggleSidebarBtn) toggleSidebarBtn.classList.add('hidden');
        if (mistakeSec) mistakeSec.classList.remove('hidden');
    }

    // paper.js 已经包装过一次 selectWorkspace，这里沿用「保存原始引用 + 包装」的写法，
    // 顺序上后加载的包装器会在最外层执行，因此必须在最后把各区块的显隐重新修正一遍
    // （paper.js 的包装器把非 paper 的工作台一律当作 bank 处理）。
    const previousSelectWorkspace = window.selectWorkspace;
    window.selectWorkspace = function (workspaceId, workspaceName) {
        if (typeof previousSelectWorkspace === 'function') {
            previousSelectWorkspace(workspaceId, workspaceName);
        }
        if (workspaceId === 'mistake') {
            showMistakeWorkspace();
            ensureMistakeWorkspaceReady();
        } else {
            const mistakeSec = el('mistakeWorkspaceSection');
            if (mistakeSec) mistakeSec.classList.add('hidden');
            stopTaskPoll();
        }
    };

    function ensureMistakeWorkspaceReady() {
        if (state.ready) { loadMistakeBatches(); return; }
        state.ready = true;
        fillSubjectOptions();
        const dateInput = el('mistakeDateInput');
        if (dateInput && !dateInput.value) dateInput.value = todayIso();
        loadMistakeBatches();
    }

    window.MistakeStore = state;
    // 纯函数单独开一个口子给 tests/js 直接断言：survivingSpans 是 JS 复刻后端
    // _subtract_manual_boxes 的区间运算，最怕两边悄悄走岔，用渲染出的 HTML 反推
    // 太绕。除此之外前端不导出内部函数。
    window.MistakeBoxMath = {
        normalizeBox: normalizeBox,
        boxTooSmall: boxTooSmall,
        survivingSpans: survivingSpans,
        columnOfBox: columnOfBox,
        snapBoxToColumn: snapBoxToColumn
    };
    window.toggleMistakeNewBatch = toggleMistakeNewBatch;
    window.submitMistakeBatch = submitMistakeBatch;
    window.openMistakeBatch = openMistakeBatch;
    window.backToMistakeList = backToMistakeList;
    window.deleteMistakeBatch = deleteMistakeBatch;
    window.startMistakeCut = startMistakeCut;
    window.toggleMistakeBoxMode = toggleMistakeBoxMode;
    window.applyMistakePageBoxes = applyMistakePageBoxes;
    window.retryMistakeBoxSave = retryMistakeBoxSave;
    window.requestMistakeMerge = requestMerge;
    window.confirmMistakeMerge = function () {
        const pending = state.mergePending;
        if (!pending) return;
        submitMerge(pending.records, pending.primary);
    };
    window.pickMergePrimary = function (index) {
        if (!state.mergePending) return;
        state.mergePending.primary = index;
        renderMergeDialog();
    };
    window.closeMistakeMergeDialog = function () {
        state.mergePending = null;
        closeMergeDialog();
    };
    window.clearMistakeMergePick = clearMergePick;
    window.jumpToMistakeMergePick = jumpToMergePick;
    window.handleMistakeCardClick = function (event, recordId) {
        // 卡片里的按钮/下拉/复选各有自己的 handler，⌘+点击也不该抢它们
        if (event.target.closest('button,input,select,label,a')) return;
        if (event.metaKey || event.ctrlKey) {
            event.preventDefault();
            toggleMergePick(recordId);
            return;
        }
        // 普通点击＝选中这一题：左侧页图滚到对应题块、闪一下并高亮，快捷键 1/2/3
        // 也作用在它身上（否则卡片点不动，只能去页图上找那一块才选得中）
        selectRecord(recordId);
    };
    window.handleMistakeThumbClick = function (event, recordId) {
        const record = findRecord(recordId);
        // ⌘/Ctrl + 点缩略图＝多选；普通点击才是「开大图」
        if (event.metaKey || event.ctrlKey) {
            event.preventDefault();
            toggleMergePick(recordId);
            return;
        }
        if (record && record.image_block) window.open(record.image_block, '_blank');
    };
    window.splitMistakeMerge = splitMistakeMerge;
    window.flipMistakeMergeOrder = flipMistakeMergeOrder;
    window.toggleMistakeMergeDirection = toggleMistakeMergeDirection;
    window.clearMistakePageBoxes = clearMistakePageBoxes;
    window.removeMistakeBox = removeMistakeBox;
    window.setMistakeLayout = setMistakeLayout;
    window.mistakePageStep = mistakePageStep;
    window.mistakePageCommit = mistakePageCommit;
    window.mistakePageInputKey = mistakePageInputKey;
    window.mistakeZoom = mistakeZoom;
    window.setMistakeCardFilter = setMistakeCardFilter;
    window.setMistakeGrad = setMistakeGrad;
    window.toggleMistakeGrad = toggleMistakeGrad;
    window.hideMistakeRecord = hideMistakeRecord;
    window.restoreHiddenBlocks = restoreHiddenBlocks;
    window.undoLastMistakeAction = undoLastAction;
    window.setMistakeInclude = setMistakeInclude;
    window.setMistakeRecordType = setMistakeRecordType;
    window.toggleMistakeReason = toggleMistakeReason;
    window.editMistakeContent = editMistakeContent;
    window.reviewMistakeAnswer = reviewMistakeAnswer;
    window.generateMistakeAnswer = generateMistakeAnswer;
    window.startMistakeRecognize = startMistakeRecognize;
    window.recognizeOneMistake = recognizeOneMistake;
    window.importMistakeBatch = importMistakeBatch;
    window.openMistakeReview = openMistakeReview;
    window.closeMistakeReview = closeMistakeReview;
    window.selectMistakeReviewRecord = selectReviewRecord;
    window.removeMistakeReviewRecord = removeMistakeReviewRecord;
    window.scheduleMistakeReviewSave = scheduleReviewSave;
    window.toggleMrPreviewAnswer = toggleMrPreviewAnswer;
    // 纯格式化函数，暴露给契约测试（mistake_preview_linebreak_check.js）与潜在跨模块复用
    window.mrContentPreviewHtml = contentPreviewHtml;
    window.setReviewOcrMode = setReviewOcrMode;
    window.runReviewAnswerOcr = runReviewAnswerOcr;
    window.renderReviewOcrModeUI = renderReviewOcrModeUI;
    window.onReviewCompulsoryChange = onReviewCompulsoryChange;
    window.onReviewChapterChange = onReviewChapterChange;
    window.onReviewRelCompulsoryChange = onReviewRelCompulsoryChange;
    window.onReviewRelChapterChange = onReviewRelChapterChange;
    window.onReviewMetaTagKey = onReviewMetaTagKey;
    window.removeReviewMetaTag = removeReviewMetaTag;
    window.addReviewRelatedChapter = addReviewRelatedChapter;
    window.removeReviewRelatedChapter = removeReviewRelatedChapter;
    window.recognizeMissingForReview = recognizeMissingForReview;
    window.recognizeCurrentReviewRecord = recognizeCurrentReviewRecord;
    window.openMistakeCropModal = openMistakeCropModal;
    window.openMistakeCropModalFromPlaceholder = openMistakeCropModalFromPlaceholder;
    window.clearMistakePlaceholders = clearMistakePlaceholders;
    window.closeMistakeCropModal = closeMistakeCropModal;
    window.mrCropGotoPage = mrCropGotoPage;
    window.mrCropStepPage = mrCropStepPage;
    window.zoomMrCropIn = zoomMrCropIn;
    window.zoomMrCropOut = zoomMrCropOut;
    window.resetMrCropZoom = resetMrCropZoom;
    window.clearMrCropSelection = clearMrCropSelection;
    window.submitMrCrop = submitMrCrop;
    window.removeMistakeFigure = removeMistakeFigure;
    window.removeInlineMistakeImage = removeInlineMistakeImage;
    window.generateReviewHandout = generateReviewHandout;
    window.openMistakeExportPanel = openMistakeExportPanel;
    window.closeMistakeExportPanel = closeMistakeExportPanel;
    window.exportMistakeHandout = exportMistakeHandout;
    window.renderMistakeWorkspace = ensureMistakeWorkspaceReady;

    document.addEventListener('DOMContentLoaded', function () {
        bindDropZone();
        bindKeyboard();
        bindReviewAnswerOcr();
        // 刷新后恢复工作台：paper.js 的恢复逻辑只认 paper，其余一律回落 bank，
        // 所以「错题工作台」的恢复必须由本模块自己处理。
        const currentServerId = window.__serverInstanceId || '';
        let savedServerId = '';
        let savedWorkspace = 'bank';
        try {
            savedServerId = localStorage.getItem('mathbank_server_instance_id') || '';
            savedWorkspace = localStorage.getItem('mathbank_active_workspace') || 'bank';
        } catch (error) { /* 隐私模式下 localStorage 不可用，忽略即可 */ }
        if (currentServerId && savedServerId === currentServerId && savedWorkspace === 'mistake') {
            if (typeof window.selectWorkspace === 'function') {
                window.selectWorkspace('mistake', '错题工作台');
            }
        }
        document.documentElement.classList.remove('init-ws-mistake');
    });
})();
