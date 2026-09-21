/**
 * paper.js - 本地化数学题库组卷系统 (Paper Studio)
 * 纯 Vanilla JS + 渐进式级联架构
 * 严禁使用正则后行断言 (?<!...) 和 (?<=...)
 */

(function () {
    'use strict';

    // Local Storage Keys
    const STORAGE_KEY_CART = 'mathbank_paper_cart';
    const STORAGE_KEY_META = 'mathbank_paper_meta';
    const STORAGE_KEY_COLLAPSED = 'mathbank_paper_filter_collapsed';
    const STORAGE_KEY_SAVED = 'mathbank_paper_saved_snapshot';

    // 学科显示名与固定顺序：与题库顶部 Tab、后端 SUBJECT_ORDER 同源。
    // 卷面正文分部分、题卡角标都读这一份，改这里等于改三处。
    const SUBJECT_LABELS = { math: '数学', physics: '物理', chemistry: '化学' };
    const SUBJECT_ORDER = ['math', 'physics', 'chemistry'];

    function subjectLabel(subject) {
        return SUBJECT_LABELS[String(subject || '').toLowerCase()] || '';
    }

    // 抬头学科行的预填值：按题库当前学科给一个起点，之后完全由用户在卷面上改。
    // 刻意不做「按卷内题目自动推断」—— 一份卷子可以同时含三科，自动推断会写错学科。
    function defaultSubjectLine() {
        return subjectLabel(window.bankSubject) || '数学';
    }

    // 页脚前缀：原来写死「数学 &nbsp;」，现跟随抬头学科行；留空则只剩页码。
    function footerSubjectPrefix(meta) {
        const label = ((meta && meta.subject_line) || '').trim();
        return label ? escapeHtml(label) + ' &nbsp; ' : '';
    }

    // 考试用时：默认 120，可改（物化单科 75、综合卷 150）。空值/非法值一律回落 120，
    // 避免打印出「考试用时 分钟」这种半截句子。
    function examDurationMinutes(meta) {
        const raw = parseInt(meta && meta.exam_duration, 10);
        return (isNaN(raw) || raw <= 0) ? 120 : raw;
    }

    // Global Store State
    window.PaperStore = {
        cart: [], // Array of { id: number, score: number }
        specRows: [], // 细目表组卷行 [{knowledge, question_type, difficulty, solve_method, count}]
        meta: {
            title: '2026年高中数学模拟考试试卷',
            subtitle: '',
            // 抬头那行学科（原先是写死的「数 学」）。默认空，首次进组卷台按题库当前学科预填一次，
            // 之后由用户直接改；一份卷子可能同时含数学/物理/化学，所以不做自动推断。
            subject_line: '',
            // 用户是否亲手改过抬头学科行：改过就永不自动回填。没改过时，卷内只有一科
            // 的话抬头/页脚会跟着卷子走（修「切到物理挑题、页脚还印数学」的学科残留）。
            subject_line_custom: false,
            exam_duration: 120,
            paper_type: 'exam_19',
            solution_space_default: '7.0',
            show_notice: true,
            show_secret: true
        },
        filters: {
            compulsory: '',
            chapter: '',
            knowledge: '',
            question_type: '',
            difficulty: '',
            keyword: '',
            subjects: SUBJECT_ORDER.slice(), // 学科多选：默认三科全选，等同改动前的「全库取题」
            tab: 'all' // 'all' or 'selected'
        },
        isFilterCollapsed: false,
        // 与试卷库里那一版的对账基准：null = 还没成功保存过卷面
        savedSignature: null,
        savedAt: null,
        saveInFlight: false,
        lastSaveError: '',
        // 组卷面板第四格「记作重做练习」的状态：{ paperId, round, pooledCount,
        // questionCount, signature, adopted }。面板每次改动都整块重建 innerHTML，
        // 状态放 DOM 上会被抹掉，只能挂在 store 里。signature 用来发现「认领之后
        // 卷面又被改过」—— 那已经是另一份卷，得重新认领。
        redoAdopt: null,
        bankQuestions: [], // Loaded questions from DB based on filters
        questionsMap: {}, // qid -> Question Object
        answerCache: Object.create(null), // qid -> full answer_markdown, loaded on demand
        expandedAnswerIds: new Set(),
        answerLoadingIds: new Set(),
        answerErrors: Object.create(null),
        activeWorkspace: 'bank'
    };

    // 旧草稿（抬头写死「数 学」那一版）里没有 subject_line 这个键 → 进组卷台时补一次预填。
    // 用户手动清空后会被存成空串，因此不会反复回填。
    let needsSubjectPrefill = false;

    // Load State from LocalStorage
    function loadStateFromStorage() {
        try {
            const rawCart = localStorage.getItem(STORAGE_KEY_CART);
            if (rawCart) {
                window.PaperStore.cart = JSON.parse(rawCart);
            }
        } catch (e) {
            window.PaperStore.cart = [];
        }

        try {
            const rawMeta = localStorage.getItem(STORAGE_KEY_META);
            if (rawMeta) {
                const parsedMeta = JSON.parse(rawMeta);
                window.PaperStore.meta = Object.assign({}, window.PaperStore.meta, parsedMeta);
                if (!parsedMeta || typeof parsedMeta !== 'object' || !('subject_line' in parsedMeta)) {
                    needsSubjectPrefill = true;
                }
            } else {
                needsSubjectPrefill = true;
            }
        } catch (e) { }

        try {
            window.PaperStore.isFilterCollapsed = localStorage.getItem(STORAGE_KEY_COLLAPSED) === 'true';
        } catch (e) { }

        // 上次成功存入试卷库时的卷面指纹，跟草稿一起跨刷新保留：否则刷新一次就会把已经
        // 归档过的卷面重新报成「未保存」，用户会重复存出多份一模一样的归档。
        try {
            const rawSaved = localStorage.getItem(STORAGE_KEY_SAVED);
            if (rawSaved) {
                const parsedSaved = JSON.parse(rawSaved);
                if (parsedSaved && typeof parsedSaved.sig === 'string') {
                    window.PaperStore.savedSignature = parsedSaved.sig;
                    window.PaperStore.savedAt = typeof parsedSaved.at === 'string' ? parsedSaved.at : null;
                }
            }
        } catch (e) { }
    }

    // 卷面的每次改动都会静静落到本机 localStorage，但只有「保存试卷」才会往数据库写归档。
    // 这两个函数是「卷面变了」的唯一收口，所以存档状态刷新挂在这里：不管调用方有没有
    // 重绘画布，提示都不会漏更新。
    function saveCartToStorage() {
        try {
            if (window.PaperStore.cart.length === 0) {
                localStorage.removeItem(STORAGE_KEY_CART);
            } else {
                localStorage.setItem(STORAGE_KEY_CART, JSON.stringify(window.PaperStore.cart));
            }
        } catch (e) { }
        updateCartBadges();
        renderPaperSaveStatus();
    }

    function saveMetaToStorage() {
        try {
            localStorage.setItem(STORAGE_KEY_META, JSON.stringify(window.PaperStore.meta));
        } catch (e) { }
        renderPaperSaveStatus();
    }

    // 卷面标题/副标题的 onblur="saveMetaToStorage()" 是写在 HTML 属性里的，靠全局查找；
    // 而函数声明在 IIFE 内部不会自动挂到 window，不显式导出会在失焦时抛 ReferenceError
    // （改动其实已被 oninput 存下了，但控制台会一直刷红）。
    window.saveCartToStorage = saveCartToStorage;
    window.saveMetaToStorage = saveMetaToStorage;

    // ---------------- 存档状态：本机草稿 vs 试卷库归档 ----------------
    // 指纹只覆盖「保存试卷」真正写进数据库的字段（Paper.title/subtitle/paper_type +
    // PaperQuestion.score）。留白高度、插图对齐都没进表，改它们不会让归档变旧，所以不参与
    // 比对——否则调一下留白就被报成「未保存」，用户白存一份重复归档。
    function computePaperSignature() {
        const meta = window.PaperStore.meta || {};
        const cart = Array.isArray(window.PaperStore.cart) ? window.PaperStore.cart : [];
        const head = [
            't:' + String(meta.title == null ? '' : meta.title),
            's:' + String(meta.subtitle == null ? '' : meta.subtitle),
            'p:' + String(meta.paper_type == null ? '' : meta.paper_type),
            'n:' + (meta.show_notice !== false ? '1' : '0'),
            'k:' + (meta.show_secret !== false ? '1' : '0')
        ].join('|');
        const body = cart.map(function (item) {
            const id = (item && item.id !== undefined) ? item.id : '';
            const score = (item && item.score !== undefined) ? item.score : '';
            return id + ':' + score;
        }).join(',');
        return head + '#' + body;
    }

    function persistSavedSnapshot() {
        try {
            localStorage.setItem(STORAGE_KEY_SAVED, JSON.stringify({
                sig: window.PaperStore.savedSignature,
                at: window.PaperStore.savedAt
            }));
        } catch (e) { }
    }

    // 保存成功后 / 载入归档后调用：把「当前卷面」登记成与库中那一份一致
    function markPaperSavedAs(atISO) {
        window.PaperStore.savedSignature = computePaperSignature();
        const at = atISO ? new Date(atISO) : new Date();
        window.PaperStore.savedAt = (at && !isNaN(at.getTime())) ? at.toISOString() : new Date().toISOString();
        window.PaperStore.lastSaveError = '';
        persistSavedSnapshot();
        renderPaperSaveStatus();
    }

    function formatSavedAtLabel(iso) {
        const d = iso ? new Date(iso) : null;
        if (!d || isNaN(d.getTime())) return '';
        const pad = function (n) { return n < 10 ? '0' + n : String(n); };
        const hm = pad(d.getHours()) + ':' + pad(d.getMinutes());
        const now = new Date();
        const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
        if (sameDay) return hm;
        const yest = new Date(now.getTime() - 86400000);
        const isYesterday = d.getFullYear() === yest.getFullYear() && d.getMonth() === yest.getMonth() && d.getDate() === yest.getDate();
        if (isYesterday) return '昨天 ' + hm;
        return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + hm;
    }

    function renderPaperSaveStatus() {
        const node = document.getElementById('paperSaveStatus');
        if (!node) return;

        const setState = function (text, cls, tip) {
            node.textContent = text;
            node.className = cls;
            if (tip) node.setAttribute('title', tip);
            else node.removeAttribute('title');
        };
        const AMBER = 'min-w-0 text-[10px] font-semibold text-amber-600 dark:text-amber-400';

        if (window.PaperStore.saveInFlight) {
            setState('保存中…', 'min-w-0 text-[10px] font-semibold text-blue-700 dark:text-blue-300', '');
            return;
        }
        if (window.PaperStore.lastSaveError) {
            setState('保存失败，请重试', 'min-w-0 text-[10px] font-semibold text-red-600 dark:text-red-400',
                window.PaperStore.lastSaveError);
            return;
        }
        if ((window.PaperStore.cart || []).length === 0) {
            setState('', '', '');
            return;
        }

        const atLabel = formatSavedAtLabel(window.PaperStore.savedAt);
        if (!window.PaperStore.savedSignature) {
            setState('未保存 · 尚未写入试卷库', AMBER,
                '这份卷面目前只在本机存了草稿。点「保存试卷」才会在试卷库里新增一条归档。');
            return;
        }
        if (computePaperSignature() !== window.PaperStore.savedSignature) {
            setState(atLabel ? ('有改动未保存 · 上次保存 ' + atLabel) : '有改动未保存', AMBER,
                '当前卷面与试卷库里那一版不一致。再点一次「保存试卷」会另存一条新归档，不会覆盖旧的那条。');
            return;
        }
        setState(atLabel ? ('已保存 · ' + atLabel) : '已保存',
            'min-w-0 text-[10px] font-semibold text-emerald-600 dark:text-emerald-400',
            '当前卷面与最后一次存入试卷库的归档一致（按归档实际保存的字段比对：标题、副标题、模板、题号与分值）。');
    }

    function getQuestionFigAlign(q) {
        // 默认居中（用户 2026-09-19 拍板）：「题干右侧 / 下方居右」变成要主动选的排版。
        // 存储值里不带 custom 标记的 'right' 是旧默认的残留，统一迁到居中。
        if (!q) return 'center';
        if (q.custom_figure_align) return q.custom_figure_align;
        if (q.figure_align && q.figure_align !== 'right') return q.figure_align;
        return 'center';
    }

    function hasCachedPaperAnswer(qid) {
        return Object.prototype.hasOwnProperty.call(window.PaperStore.answerCache, qid);
    }

    function seedPaperAnswerCache(q) {
        if (!q || !q.id || typeof q.answer_markdown !== 'string') return;
        window.PaperStore.answerCache[q.id] = q.answer_markdown;
        q.has_answer = Boolean(q.answer_markdown.trim());
    }

    async function loadPaperQuestionAnswer(qid) {
        qid = parseInt(qid, 10);
        if (!qid || window.PaperStore.answerLoadingIds.has(qid)) return;

        const q = window.PaperStore.questionsMap[qid];
        if (q) seedPaperAnswerCache(q);
        if (hasCachedPaperAnswer(qid)) {
            renderPart3QuestionStream();
            return;
        }

        window.PaperStore.answerLoadingIds.add(qid);
        delete window.PaperStore.answerErrors[qid];
        renderPart3QuestionStream();

        try {
            const response = await fetch(`/api/questions/${qid}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const detail = await response.json();
            const answer = typeof detail.answer_markdown === 'string'
                ? detail.answer_markdown
                : '';
            window.PaperStore.answerCache[qid] = answer;
            if (q) {
                q.answer_markdown = answer;
                q.has_answer = Boolean(answer.trim());
            }
        } catch (error) {
            console.error('Load paper question answer error:', error);
            window.PaperStore.answerErrors[qid] = '答案加载失败，请重试。';
        } finally {
            window.PaperStore.answerLoadingIds.delete(qid);
            renderPart3QuestionStream();
        }
    }

    window.togglePaperQuestionAnswer = function (qid) {
        // 改为打开详情弹窗查看完整题目与答案 (虚拟滚动下列表不再就地展开)
        window.openQuestionDetail(qid);
    };

    window.retryPaperQuestionAnswer = function (qid) {
        qid = parseInt(qid, 10);
        if (!qid) return;
        delete window.PaperStore.answerErrors[qid];
        delete window.PaperStore.answerCache[qid];
        loadPaperQuestionAnswer(qid);
    };

    window.collapseAllPaperAnswers = function () {
        window.PaperStore.expandedAnswerIds.clear();
        renderPart3QuestionStream();
    };

    // Public Cart Helper Functions
    window.isInCart = function (qid) {
        qid = parseInt(qid, 10);
        return window.PaperStore.cart.some(item => item.id === qid);
    };

    /**
     * 批量入篮：错题工作台「入库后送入组卷」用。
     *
     * 与 addToCart 的差别只有一个：整批只写一次 localStorage、只重渲染一次、
     * 只弹一条提示。逐题调 addToCart 会一次入库 20 道就打出 20 条 toast。
     *
     * pairs: [{id, score}]；已在本篮里的 id 会跳过。返回实际新增的道数。
     */
    window.addManyToCart = function (pairs) {
        const added = [];
        (pairs || []).forEach(function (pair) {
            const qid = parseInt(pair && pair.id, 10);
            if (!qid || window.isInCart(qid)) return;
            window.PaperStore.cart.push({ id: qid, score: parseInt(pair.score, 10) || 5 });
            added.push(qid);
        });
        if (added.length) {
            saveCartToStorage();
            syncSubjectLineWithCart();
            updateCartBadges();
            renderPart3QuestionStream();
            if (window.PaperStore.activeWorkspace === 'paper' && typeof window.renderPaperCanvas === 'function') {
                window.renderPaperCanvas();
            }
        }
        return added.length;
    };

    window.addToCart = function (qid, score = null) {
        qid = parseInt(qid, 10);
        if (!window.isInCart(qid)) {
            const q = window.PaperStore.questionsMap[qid];
            if (!score && q) {
                score = q.question_type === 'detailed_answer' ? 12 : 5;
            } else if (!score) {
                score = 5;
            }
            window.PaperStore.cart.push({ id: qid, score: parseInt(score, 10) || 5 });
            saveCartToStorage();
            syncSubjectLineWithCart();
            const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
            if (window.showToast) window.showToast(`已将题目 #${seqNum} 加入试卷`, 'success');
            
            if (window.PaperStore.activeWorkspace === 'paper') {
                renderPart3QuestionStream();
                window.renderPaperCanvas();
            }
        }
    };

    window.removeFromCart = function (qid) {
        qid = parseInt(qid, 10);
        window.PaperStore.cart = window.PaperStore.cart.filter(item => item.id !== qid);
        saveCartToStorage();
        syncSubjectLineWithCart();
        if (window.PaperStore.activeWorkspace === 'paper') {
            renderPart3QuestionStream();
            window.renderPaperCanvas();
        }
        const q = window.PaperStore.questionsMap[qid];
        const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
        if (window.showToast) window.showToast(`已将题目 #${seqNum} 移出试卷`, 'info');
    };

    window.toggleCart = function (qid, score = null) {
        qid = parseInt(qid, 10);
        if (window.isInCart(qid)) {
            window.removeFromCart(qid);
        } else {
            window.addToCart(qid, score);
        }
    };

    window.clearCart = function () {
        if (confirm('确定要清空已加入试卷的所有题目吗？')) {
            window.PaperStore.cart = [];
            // 清空卷面等于开一张新卷：抬头学科行回到「按题库当前学科预填」的起点，
            // 免得上一张卷的学科残留在这张新卷上。
            window.PaperStore.meta.subject_line = defaultSubjectLine();
            saveCartToStorage();
            saveMetaToStorage();
            if (window.PaperStore.activeWorkspace === 'paper') {
                renderPart3QuestionStream();
                window.renderPaperCanvas();
            }
            if (window.showToast) window.showToast('已清空试卷题目', 'info');
        }
    };

    function updateCartBadges() {
        const count = window.PaperStore.cart.length;
        const badges = document.querySelectorAll('.paper-cart-badge');
        badges.forEach(b => {
            b.textContent = count;
            if (count > 0) {
                b.classList.remove('hidden');
            } else {
                b.classList.add('hidden');
            }
        });
        // 题库工作台顶部的"试题篮"提示条：只要购物车非空就显示，方便随时切到组卷
        const hintBar = document.getElementById('bankCartHintBar');
        if (hintBar) {
            if (count > 0) {
                hintBar.classList.remove('hidden');
            } else {
                hintBar.classList.add('hidden');
            }
        }
    }

    // Workspace View Switcher
    const originalSelectWorkspace = window.selectWorkspace;
    window.selectWorkspace = function (workspaceId, workspaceName) {
        if (typeof originalSelectWorkspace === 'function') {
            originalSelectWorkspace(workspaceId, workspaceName);
        }

        window.PaperStore.activeWorkspace = workspaceId;
        try {
            localStorage.setItem('mathbank_active_workspace', workspaceId);
            if (window.__serverInstanceId) {
                localStorage.setItem('mathbank_server_instance_id', window.__serverInstanceId);
            }
        } catch (e) { }

        const bankSec = document.getElementById('bankWorkspaceSection');
        const paperSec = document.getElementById('paperWorkspaceSection');
        const toggleSidebarBtn = document.getElementById('toggleSidebarBtn');
        const topFilterBar = document.getElementById('bankTopFilterBar');

        if (workspaceId === 'paper') {
            if (bankSec) bankSec.classList.add('hidden');
            if (topFilterBar) topFilterBar.classList.add('hidden');
            if (toggleSidebarBtn) toggleSidebarBtn.classList.add('hidden');
            if (paperSec) {
                paperSec.classList.remove('hidden');
                window.renderPaperWorkspace();
            }
        } else {
            if (paperSec) paperSec.classList.add('hidden');
            if (topFilterBar) topFilterBar.classList.remove('hidden');
            if (toggleSidebarBtn) toggleSidebarBtn.classList.remove('hidden');
            if (bankSec) bankSec.classList.remove('hidden');
        }
    };

    // Toggle Part 2 Filter Bar Collapsing
    window.togglePaperFilterBar = function () {
        window.PaperStore.isFilterCollapsed = !window.PaperStore.isFilterCollapsed;
        try {
            localStorage.setItem(STORAGE_KEY_COLLAPSED, window.PaperStore.isFilterCollapsed ? 'true' : 'false');
        } catch (e) { }

        const filterBox = document.getElementById('paperFilterSection');
        const toggleIcon = document.getElementById('paperFilterToggleIcon');
        const toggleTxt = document.getElementById('paperFilterToggleTxt');

        if (!filterBox) return;

        if (window.PaperStore.isFilterCollapsed) {
            filterBox.style.maxHeight = '0px';
            filterBox.style.opacity = '0';
            filterBox.style.overflow = 'hidden';
            filterBox.style.paddingTop = '0px';
            filterBox.style.paddingBottom = '0px';
            filterBox.style.marginTop = '0px';
            filterBox.style.marginBottom = '0px';
            if (toggleIcon) toggleIcon.className = 'fa-solid fa-chevron-down text-xs';
            if (toggleTxt) toggleTxt.textContent = '展开组卷配置栏';
        } else {
            filterBox.style.maxHeight = '700px';
            filterBox.style.opacity = '1';
            filterBox.style.overflow = '';
            filterBox.style.paddingTop = '';
            filterBox.style.paddingBottom = '';
            filterBox.style.marginTop = '';
            filterBox.style.marginBottom = '';
            if (toggleIcon) toggleIcon.className = 'fa-solid fa-chevron-up text-xs';
            if (toggleTxt) toggleTxt.textContent = '收起组卷配置栏';
        }
    };

    // Move Question Order
    window.movePaperQuestion = function (index, direction) {
        const cart = window.PaperStore.cart;
        if (direction === 'up' && index > 0) {
            const temp = cart[index];
            cart[index] = cart[index - 1];
            cart[index - 1] = temp;
            saveCartToStorage();
            renderPart3QuestionStream();
            window.renderPaperCanvas();
        } else if (direction === 'down' && index < cart.length - 1) {
            const temp = cart[index];
            cart[index] = cart[index + 1];
            cart[index + 1] = temp;
            saveCartToStorage();
            renderPart3QuestionStream();
            window.renderPaperCanvas();
        }
    };

    // Update Score
    window.updatePaperQuestionScore = function (qid, newScore) {
        qid = parseInt(qid, 10);
        newScore = parseInt(newScore, 10) || 5;
        const item = window.PaperStore.cart.find(it => it.id === qid);
        if (item) {
            item.score = newScore;
            saveCartToStorage();
            window.renderPaperCanvas();
        }
    };

    // 组卷页的学科勾选（默认三科全选）。返回值恒为有序、去重的合法学科 key，
    // 调用方不必再操心「数组被写坏」「顺序乱掉」这类情况。
    function paperFilterSubjects() {
        const raw = window.PaperStore.filters.subjects;
        const list = Array.isArray(raw) ? raw : SUBJECT_ORDER.slice();
        return SUBJECT_ORDER.filter(s => list.indexOf(s) >= 0);
    }

    // 把若干学科的教材树合成一棵：同学段 / 同章节的 key 合并，小节去重。
    // 单科时结果与 subjectTreeFor(单科) 同构 —— 单科用户看到的下拉内容与改动前一致。
    function mergedSubjectTree(subjectKeys) {
        const merged = {};
        (subjectKeys || []).forEach(subjectKey => {
            let one = null;
            if (typeof subjectTreeFor === 'function') one = subjectTreeFor(subjectKey);
            if (!one || typeof one !== 'object') one = window.categoryTree || null;
            if (!one || typeof one !== 'object') return;
            Object.keys(one).forEach(book => {
                if (!merged[book]) merged[book] = {};
                const chapters = one[book] || {};
                Object.keys(chapters).forEach(ch => {
                    const knowList = Array.isArray(chapters[ch]) ? chapters[ch] : [];
                    if (!merged[book][ch]) merged[book][ch] = [];
                    knowList.forEach(k => {
                        if (merged[book][ch].indexOf(k) < 0) merged[book][ch].push(k);
                    });
                });
            });
        });
        return merged;
    }

    // 卷内出现过的学科（按固定顺序、去重）。
    function cartSubjects() {
        const seen = {};
        window.PaperStore.cart.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            if (!q) return;
            seen[String(q.subject || 'math').toLowerCase()] = true;
        });
        return SUBJECT_ORDER.filter(s => seen[s])
            .concat(Object.keys(seen).filter(s => SUBJECT_ORDER.indexOf(s) < 0));
    }

    // 抬头/页脚学科行跟随卷内学科：仅当用户没亲手改过（subject_line_custom）且
    // 卷内恰好一科时生效；空卷与混科卷一律不动（混科卷学科写什么该由用户定）。
    // 修的是「题库切到物理挑了 20 题，卷尾页脚还印着上一张卷的数学」。
    function syncSubjectLineWithCart() {
        const meta = window.PaperStore.meta;
        if (meta.subject_line_custom) return;
        const labeled = cartSubjects().filter(s => SUBJECT_LABELS[s]);
        if (labeled.length !== 1) return;
        const label = SUBJECT_LABELS[labeled[0]];
        if ((meta.subject_line || '').trim() !== label) {
            meta.subject_line = label;
            saveMetaToStorage();
        }
    }

    // 导出/保存时实际使用的模板：混科（卷内 ≥2 科）时把 exam_19 降级成普通考试卷，
    // 与卷面预览的渲染口径一致 —— 否则会出现「预览题号连续、导出 PDF 却跳号」。
    function paperTypeForPayload() {
        const type = window.PaperStore.meta.paper_type || 'exam';
        if (type !== 'exam_19') return type;
        return cartSubjects().length > 1 ? 'exam' : type;
    }

    // Fetch Questions from DB for Question Bank Stream
    async function fetchBankQuestions() {
        const f = window.PaperStore.filters;
        const params = new URLSearchParams();
        if (f.compulsory) {
            params.append('compulsory', f.compulsory);
            params.append('category_compulsory', f.compulsory);
        }
        if (f.chapter) {
            params.append('chapter', f.chapter);
            params.append('category_chapter', f.chapter);
        }
        if (f.knowledge) {
            params.append('knowledge', f.knowledge);
            params.append('category_knowledge', f.knowledge);
        }
        if (f.question_type) {
            params.append('qtype', f.question_type);
            params.append('question_type', f.question_type);
        }
        if (f.difficulty) {
            params.append('difficulty', f.difficulty);
        }
        if (f.keyword) {
            params.append('q', f.keyword);
            params.append('search', f.keyword);
        }
        // 学科多选：三科全选时不传参数（等同原来的全库，库里万一有陌生学科值也不会漏），
        // 只勾了部分学科才带上，避免「勾了数学却混进物理题」。
        const pickedSubjects = paperFilterSubjects();
        if (pickedSubjects.length > 0 && pickedSubjects.length < SUBJECT_ORDER.length) {
            params.append('subject', pickedSubjects.join(','));
        }

        try {
            const res = await fetch(`/api/questions?${params.toString()}`);
            const questions = await res.json();
            if (Array.isArray(questions)) {
                window.PaperStore.bankQuestions = questions;
                questions.forEach(q => {
                    window.PaperStore.questionsMap[q.id] = q;
                });
            }
        } catch (e) {
            console.error('Fetch bank questions error:', e);
        }
    }

    // Render Full Paper Workspace (Part 2, Part 3, Part 4)
    window.renderPaperWorkspace = async function () {
        await fetchBankQuestions();
        syncSubjectLineWithCart();
        renderPart2FilterSection();
        renderPart3QuestionStream();
        window.renderPaperCanvas();

        // 抬头学科行的预填时机放在这里：进组卷台时题库学科一定已就绪，脚本加载期读它不可靠。
        // 只补一次，补完立刻落盘，之后用户怎么改都不会被回填。
        if (needsSubjectPrefill) {
            needsSubjectPrefill = false;
            if (!(window.PaperStore.meta.subject_line || '').trim()) {
                window.PaperStore.meta.subject_line = defaultSubjectLine();
                saveMetaToStorage();
                window.renderPaperCanvas();
            }
        }
    };

    // 组卷体检报告渲染（供弹窗复用）
    function renderHealthReportInto(container) {
        if (!container) return;
        const cart = window.PaperStore.cart;
        const meta = window.PaperStore.meta;
        const qmap = window.PaperStore.questionsMap;

        const targetScore = parseInt(meta.total_score_target, 10);
        const validItems = cart.filter(it => {
            const q = qmap[it.id];
            return q && q.content && q.content.trim().length > 0;
        });

        const totalCount = validItems.length;
        const totalScore = validItems.reduce((s, it) => s + (parseInt(it.score, 10) || 5), 0);

        // Type distribution
        const typeLabels = {
            single_choice: '单选', multi_choice: '多选',
            fill_in_blank: '填空', detailed_answer: '解答'
        };
        const typeCount = { single_choice: 0, multi_choice: 0, fill_in_blank: 0, detailed_answer: 0 };
        const typeScore = { single_choice: 0, multi_choice: 0, fill_in_blank: 0, detailed_answer: 0 };
        // Difficulty distribution — group by the canonical difficulty values
        // from systemMetadata (easy_error / normal / challenge / qiangji).
        const diffMetaList = (window.systemMetadata && window.systemMetadata.difficulties) || [
            { value: 'easy_error', label: '易错题' },
            { value: 'normal', label: '常规题' },
            { value: 'challenge', label: '挑战题' },
            { value: 'qiangji', label: '强基题' }
        ];
        const diffCount = {};
        diffMetaList.forEach(d => { diffCount[d.value] = 0; });
        diffCount.__unknown = 0;
        // 3-tier mapping for the stacked 易/中/难 bar:
        //   易 = 常规题 + 易错题, 中 = 挑战题, 难 = 强基题 (computed below)
        // Knowledge coverage
        const knowSet = new Set();

        validItems.forEach(it => {
            const q = qmap[it.id];
            if (!q) return;
            const t = q.question_type || 'single_choice';
            if (typeCount[t] !== undefined) {
                typeCount[t]++;
                typeScore[t] += (parseInt(it.score, 10) || 5);
            }
            const d = q.difficulty;
            if (diffCount[d] !== undefined) diffCount[d]++;
            else diffCount.__unknown++;
            const kl = q.knowledge_list || q.category_knowledge || '';
            kl.split(/[,，;；\n]+/).forEach(k => {
                const kk = k.trim();
                if (kk) knowSet.add(kk);
            });
        });

        // Score target check
        let scoreCheckHtml = '';
        if (!isNaN(targetScore) && targetScore > 0) {
            if (totalScore === targetScore) {
                scoreCheckHtml = `<span class="text-emerald-600 font-semibold"><i class="fa-solid fa-circle-check mr-1"></i>已对齐目标 ${targetScore} 分</span>`;
            } else {
                const diff = totalScore - targetScore;
                scoreCheckHtml = `<span class="text-amber-600 font-semibold"><i class="fa-solid fa-triangle-exclamation mr-1"></i>与目标差 ${diff > 0 ? '+' : ''}${diff} 分（当前 ${totalScore} / 目标 ${targetScore}）</span>`;
            }
        } else {
            scoreCheckHtml = `<span class="text-slate-400">当前总分 ${totalScore} 分（可在下方设置目标分校验）</span>`;
        }

        // Type distribution chips
        const typeChip = (key) => {
            const n = typeCount[key];
            if (!n) return '';
            return `<span class="inline-flex items-center px-2 py-0.5 rounded-lg bg-brand-50 text-brand-700 text-[10px] font-semibold border border-brand-200/60 dark:bg-brand-900/40 dark:text-brand-200 dark:border-brand-900">${typeLabels[key]} ${n}题/${typeScore[key]}分</span>`;
        };
        const typeDistHtml = (typeCount.single_choice || typeCount.multi_choice || typeCount.fill_in_blank || typeCount.detailed_answer)
            ? `<div class="flex flex-wrap gap-1.5 mt-1">${typeChip('single_choice')}${typeChip('multi_choice')}${typeChip('fill_in_blank')}${typeChip('detailed_answer')}</div>`
            : `<div class="text-[10px] text-slate-400 mt-1">暂无题型分布</div>`;

        // Difficulty bar (3-tier: 易 = 常规+易错, 中 = 挑战, 难 = 强基)
        const diffTotal = totalCount || 1;
        const easyN = (diffCount.easy_error || 0) + (diffCount.normal || 0);
        const medN = (diffCount.challenge || 0);
        const hardN = (diffCount.qiangji || 0);
        const easyPct = Math.round((easyN / diffTotal) * 100);
        const medPct = Math.round((medN / diffTotal) * 100);
        const hardPct = Math.max(0, 100 - easyPct - medPct);
        const diffBarHtml = totalCount > 0 ? `
            <div class="mt-1">
                <div class="flex items-center justify-between text-[10px] text-slate-500 mb-0.5">
                    <span>难度分布</span>
                    <span>易 ${easyPct}% · 中 ${medPct}% · 难 ${hardPct}%</span>
                </div>
                <div class="w-full h-2 rounded-full bg-slate-200 overflow-hidden flex dark:bg-slate-700">
                    <div class="bg-emerald-500 h-full" style="width:${easyPct}%"></div>
                    <div class="bg-amber-500 h-full" style="width:${medPct}%"></div>
                    <div class="bg-rose-500 h-full" style="width:${hardPct}%"></div>
                </div>
            </div>` : '';

        // Knowledge coverage
        const knowArr = Array.from(knowSet);
        const knowHtml = knowArr.length > 0
            ? `<div class="flex flex-wrap gap-1 mt-1">${knowArr.slice(0, 12).map(k => `<span class="inline-flex items-center px-1.5 py-0.5 rounded-md bg-slate-100 text-slate-600 text-[10px] dark:bg-slate-800 dark:text-slate-300">${escapeHtml(k)}</span>`).join('')}${knowArr.length > 12 ? `<span class="text-[10px] text-slate-400">+${knowArr.length - 12}</span>` : ''}</div>`
            : `<div class="text-[10px] text-slate-400 mt-1">本卷尚未标注知识点</div>`;

        // Duplicate detection (same source + same leading content)
        const seenKeys = {};
        const dupList = [];
        validItems.forEach(it => {
            const q = qmap[it.id];
            if (!q) return;
            const src = (q.source || '').trim();
            const head = (q.content || '').replace(/\s+/g, '').slice(0, 40);
            const key = (src ? src + '|' : '') + head;
            if (seenKeys[key]) {
                dupList.push(it.id);
            } else {
                seenKeys[key] = true;
            }
        });
        const dupHtml = dupList.length > 0
            ? `<div class="mt-1 text-[10px] text-rose-600 font-semibold"><i class="fa-solid fa-copy mr-1"></i>疑似重复 ${dupList.length} 道（同源/同题干开头），建议检查</div>`
            : (totalCount > 0 ? `<div class="mt-1 text-[10px] text-emerald-600"><i class="fa-solid fa-circle-check mr-1"></i>未检出明显重复题</div>` : '');

        container.innerHTML = `
            <div class="space-y-4">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-1.5 text-sm font-bold text-slate-700 dark:text-slate-200">
                        <i class="fa-solid fa-stethoscope text-brand-500"></i>
                        <span>组卷体检报告</span>
                        <span class="text-slate-400 font-normal">${totalCount} 题</span>
                    </div>
                    <div class="flex items-center space-x-1 text-[11px]">
                        <span class="text-slate-400">目标分</span>
                        <input id="paperTargetScoreInput" type="number" min="0" max="300" value="${isNaN(targetScore) ? '' : targetScore}" placeholder="目标分" class="w-16 px-1.5 py-0.5 text-[11px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 focus:ring-1 focus:ring-brand-500 focus:outline-none" onchange="window.updatePaperMeta('total_score_target', this.value ? parseInt(this.value,10) : '')" />
                    </div>
                </div>
                <div class="text-sm">${scoreCheckHtml}</div>
                <div>
                    <div class="text-[11px] font-semibold text-slate-500 dark:text-slate-400 mb-1">题型分布</div>
                    ${typeDistHtml}
                </div>
                <div>
                    <div class="text-[11px] font-semibold text-slate-500 dark:text-slate-400 mb-1">难度分布</div>
                    ${diffBarHtml || '<div class="text-[10px] text-slate-400">暂无题目</div>'}
                </div>
                <div>
                    <div class="text-[11px] font-semibold text-slate-500 dark:text-slate-400 mb-1">知识点覆盖（${knowArr.length} 个）</div>
                    ${knowHtml}
                </div>
                ${dupHtml || (totalCount === 0 ? '<div class="text-[10px] text-slate-400">尚未选题，无法体检</div>' : '')}
            </div>
        `;
    }

    // 打开组卷体检弹窗
    window.openHealthModal = function () {
        let modal = document.getElementById('healthModal');
        if (modal) {
            window.MathBankModal.close(modal);
            modal.remove();
        }
        modal = document.createElement('div');
        modal.id = 'healthModal';
        modal.className = 'fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-md flex items-center justify-center p-4 animate-in fade-in duration-200';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'healthModalTitle');
        modal.innerHTML = `
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl shadow-2xl w-full max-w-lg max-h-[85vh] flex flex-col overflow-hidden font-sans">
                <div class="px-6 py-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-slate-50/50 dark:bg-slate-800/50">
                    <div class="flex items-center space-x-2.5">
                        <div class="w-9 h-9 rounded-2xl bg-brand-500/10 text-brand-600 dark:text-brand-400 flex items-center justify-center font-bold text-lg">
                            <i class="fa-solid fa-stethoscope"></i>
                        </div>
                        <div>
                            <h3 id="healthModalTitle" class="font-bold text-slate-800 dark:text-slate-100 text-base">组卷体检</h3>
                            <p class="text-xs text-slate-400">检查总分对齐、题型/难度/知识点分布与重复题</p>
                        </div>
                    </div>
                    <button type="button" onclick="closeHealthModal()" aria-label="关闭" class="w-8 h-8 rounded-full hover:bg-slate-200/60 dark:hover:bg-slate-700 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 flex items-center justify-center transition-colors">
                        <i class="fa-solid fa-xmark text-sm"></i>
                    </button>
                </div>
                <div class="p-6 overflow-y-auto flex-1" id="healthModalBody"></div>
                <div class="px-6 py-3.5 border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-800/50 flex items-center justify-end">
                    <button onclick="closeHealthModal()" class="px-4 py-1.5 rounded-xl bg-slate-200 text-slate-700 hover:bg-slate-300 font-medium transition-colors dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700">关闭</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        window.MathBankModal.open(modal, { onEscape: window.closeHealthModal });
        renderHealthReportInto(document.getElementById('healthModalBody'));
    };

    window.closeHealthModal = function () {
        const modal = document.getElementById('healthModal');
        if (modal) {
            window.MathBankModal.close(modal);
            modal.remove();
        }
    };

    // 兼容旧调用（保留别名）
    window.renderPaperHealthCheck = function () {
        const c = document.getElementById('healthModalBody') || document.getElementById('paperToolTabHealthContent');
        renderHealthReportInto(c);
    };


    // ---- 试卷模板与预设 (Templates & personal presets) ----
    const BUILTIN_TEMPLATES = [
        {
            key: 'weekly', name: '周测卷', desc: '100分·选择+填空+解答',
            meta: { paper_type: 'exam', total_score_target: 100, solution_space_default: '7.0' },
            specRows: [
                { knowledge: '', question_type: 'single_choice', difficulty: '', solve_method: '', count: 8 },
                { knowledge: '', question_type: 'fill_in_blank', difficulty: '', solve_method: '', count: 4 },
                { knowledge: '', question_type: 'detailed_answer', difficulty: '', solve_method: '', count: 4 }
            ]
        },
        {
            key: 'monthly', name: '月考卷', desc: '150分·高考式',
            meta: { paper_type: 'exam_19', total_score_target: 150, solution_space_default: '7.0' },
            specRows: [
                { knowledge: '', question_type: 'single_choice', difficulty: '', solve_method: '', count: 8 },
                { knowledge: '', question_type: 'multi_choice', difficulty: '', solve_method: '', count: 4 },
                { knowledge: '', question_type: 'fill_in_blank', difficulty: '', solve_method: '', count: 4 },
                { knowledge: '', question_type: 'detailed_answer', difficulty: '', solve_method: '', count: 6 }
            ]
        },
        {
            key: 'classroom', name: '随堂练', desc: '50分·纯解答',
            meta: { paper_type: 'quiz', total_score_target: 50, solution_space_default: '7.0' },
            specRows: [
                { knowledge: '', question_type: 'detailed_answer', difficulty: '', solve_method: '', count: 4 }
            ]
        }
    ];

    function applyTemplateConfig(tpl, loadSpec) {
        // 仅覆盖卷面参数（版式/总分/留白），不动已选试题篮
        window.PaperStore.meta.paper_type = tpl.meta.paper_type;
        window.PaperStore.meta.total_score_target = tpl.meta.total_score_target;
        window.PaperStore.meta.solution_space_default = tpl.meta.solution_space_default;
        if (loadSpec && Array.isArray(tpl.specRows) && tpl.specRows.length > 0) {
            // 仅把细目表载入工作区，供用户在细目表弹窗里查看/修改/手动抽题
            window.PaperStore.specRows = JSON.parse(JSON.stringify(tpl.specRows));
        }
        saveMetaToStorage();
        saveCartToStorage();
        window.renderPaperCanvas();
    }

    window.renderPaperTemplatePanel = function () {
        window.openTemplateModal();
    };

    // ---- 模板弹窗 ----
    window.openTemplateModal = function () {
        let modal = document.getElementById('templateModal');
        if (modal) {
            window.MathBankModal.close(modal);
            modal.remove();
        }
        modal = document.createElement('div');
        modal.id = 'templateModal';
        modal.className = 'fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-md flex items-center justify-center p-4 animate-in fade-in duration-200';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'templateModalTitle');
        modal.innerHTML = `
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden font-sans">
                <div class="px-6 py-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-slate-50/50 dark:bg-slate-800/50">
                    <div class="flex items-center space-x-2.5">
                        <div class="w-9 h-9 rounded-2xl bg-brand-500/10 text-brand-600 dark:text-brand-400 flex items-center justify-center font-bold text-lg">
                            <i class="fa-solid fa-layer-group"></i>
                        </div>
                        <div>
                            <h3 id="templateModalTitle" class="font-bold text-slate-800 dark:text-slate-100 text-base">选择试卷模板</h3>
                            <p class="text-xs text-slate-400">套用卷面参数（版式/总分/留白），细目表可选加载</p>
                        </div>
                    </div>
                    <button type="button" onclick="closeTemplateModal()" aria-label="关闭" class="w-8 h-8 rounded-full hover:bg-slate-200/60 dark:hover:bg-slate-700 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 flex items-center justify-center transition-colors">
                        <i class="fa-solid fa-xmark text-sm"></i>
                    </button>
                </div>
                <div class="p-6 overflow-y-auto flex-1 space-y-5" id="templateModalBody"></div>
                <div class="px-6 py-3.5 border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-800/50 flex items-center justify-end gap-3">
                    <button onclick="window.openTemplateEditor()" class="px-4 py-1.5 rounded-xl text-xs font-semibold bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 active:scale-95 transition-all flex items-center space-x-1.5">
                        <i class="fa-solid fa-plus"></i><span>新增预设</span>
                    </button>
                    <button onclick="closeTemplateModal()" class="px-4 py-1.5 rounded-xl bg-slate-200 text-slate-700 hover:bg-slate-300 font-medium transition-colors dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700">关闭</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        window.MathBankModal.open(modal, { onEscape: window.closeTemplateModal });
        window.renderTemplateList();
    };

    window.closeTemplateModal = function () {
        const modal = document.getElementById('templateModal');
        if (modal) {
            window.MathBankModal.close(modal);
            modal.remove();
        }
    };

    // 渲染模板列表（内置 + 个人）
    window.renderTemplateList = function () {
        const body = document.getElementById('templateModalBody');
        if (!body) return;
        const builtinHtml = BUILTIN_TEMPLATES.map(t => `
            <div class="flex items-center justify-between bg-slate-50 dark:bg-slate-800/60 rounded-xl px-3 py-2.5">
                <div class="min-w-0">
                    <div class="text-xs font-bold text-slate-700 dark:text-slate-200">${escapeHtml(t.name)}</div>
                    <div class="text-[10px] text-slate-400">${escapeHtml(t.desc)}</div>
                </div>
                <div class="flex items-center gap-1.5 shrink-0">
                    <button onclick="window.applyTemplateFlow('builtin','${t.key}')" class="px-2.5 py-1 rounded-lg text-[10px] font-semibold bg-brand-600 text-white hover:bg-brand-700 active:scale-95 transition-all">应用</button>
                    <button onclick="window.openTemplateEditor('builtin','${t.key}')" class="px-2.5 py-1 rounded-lg text-[10px] font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700" title="基于该模板编辑">编辑</button>
                </div>
            </div>
        `).join('');

        body.innerHTML = `
            <div>
                <div class="text-[11px] font-bold text-slate-500 dark:text-slate-400 mb-2">内置模板</div>
                <div class="space-y-2">${builtinHtml}</div>
            </div>
            <div>
                <div class="text-[11px] font-bold text-slate-500 dark:text-slate-400 mb-2">个人预设</div>
                <div id="personalTemplateList" class="space-y-2">
                    <div class="text-[10px] text-slate-400">加载中…</div>
                </div>
            </div>
        `;
        window.loadPersonalTemplates();
    };

    // 应用模板流程：先展示确认（含细目表勾选）
    window.applyTemplateFlow = async function (kind, keyOrId) {
        let tpl = null;
        if (kind === 'builtin') {
            tpl = BUILTIN_TEMPLATES.find(t => t.key === keyOrId);
        } else {
            try {
                const res = await fetch('/api/paper/templates');
                const data = await res.json();
                const list = data.data || [];
                const raw = list.find(t => t.id === keyOrId);
                if (!raw) return;
                tpl = {
                    name: raw.name,
                    meta: { paper_type: raw.paper_type, total_score_target: raw.total_score_target, solution_space_default: '7.0' },
                    specRows: raw.spec_rows && raw.spec_rows.length ? raw.spec_rows : []
                };
            } catch (e) { return; }
        }
        if (!tpl) return;

        const body = document.getElementById('templateModalBody');
        if (!body) return;
        const hasSpec = Array.isArray(tpl.specRows) && tpl.specRows.length > 0;
        body.innerHTML = `
            <div class="space-y-4">
                <div class="flex items-center gap-2">
                    <button onclick="window.renderTemplateList()" class="text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"><i class="fa-solid fa-arrow-left mr-1"></i>返回</button>
                    <div class="text-sm font-bold text-slate-700 dark:text-slate-200">应用「${escapeHtml(tpl.name)}」</div>
                </div>
                <div class="text-xs text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-800/60 rounded-xl p-3 space-y-1">
                    <div>版式：${tpl.meta.paper_type === 'exam_19' ? '19题高考卷(含答题卡)' : tpl.meta.paper_type === 'quiz' ? '日常小练' : '常规试卷'}</div>
                    <div>目标总分：${escapeHtml(String(tpl.meta.total_score_target || ''))} 分</div>
                    <div>默认留白：${parseFloat(tpl.meta.solution_space_default || '7.0') === 0 ? '不留白' : parseFloat(tpl.meta.solution_space_default || '7.0') + ' cm'}</div>
                    ${hasSpec ? `<div>细目表：含 ${tpl.specRows.length} 行条件</div>` : '<div>细目表：无</div>'}
                </div>
                ${hasSpec ? `
                <label class="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-300 bg-brand-50/50 dark:bg-brand-900/30 rounded-xl p-3 cursor-pointer">
                    <input type="checkbox" id="tmplLoadSpecChk" class="mt-0.5 w-4 h-4 rounded accent-brand-600" />
                    <span>同时加载模板细目表（仅载入细目表弹窗，不会自动抽题；你可在细目表里改知识点/难度后再手动抽题）</span>
                </label>
                ` : ''}
                <div class="flex items-center justify-end gap-3 pt-1">
                    <button onclick="window.renderTemplateList()" class="px-4 py-1.5 rounded-xl bg-slate-200 text-slate-700 hover:bg-slate-300 font-medium dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700">取消</button>
                    <button onclick="window.confirmApplyTemplate(${hasSpec ? 'true' : 'false'})" class="px-4 py-1.5 rounded-xl bg-brand-600 text-white font-semibold hover:bg-brand-700 active:scale-95 transition-all">确认应用</button>
                </div>
                <div id="tmplApplyMsg" class="text-[10px] text-slate-500"></div>
            </div>
        `;
        // 暂存待应用模板
        window.__pendingTemplate = tpl;
    };

    window.confirmApplyTemplate = function (hasSpec) {
        const tpl = window.__pendingTemplate;
        if (!tpl) return;
        const loadSpec = hasSpec ? (document.getElementById('tmplLoadSpecChk') || {}).checked : false;
        applyTemplateConfig(tpl, loadSpec);
        if (window.showToast) window.showToast(`已套用「${tpl.name}」模板（${loadSpec ? '已载入细目表' : '仅卷面参数'}）`, 'success');
        window.closeTemplateModal();
    };

    window.applyBuiltinTemplate = function (key) {
        window.applyTemplateFlow('builtin', key);
    };

    window.saveCurrentAsTemplate = async function () {
        const name = prompt('请输入预设名称：', (window.PaperStore.meta.title || '我的预设'));
        if (!name) return;
        try {
            const res = await fetch('/api/paper/save-template', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    meta: window.PaperStore.meta,
                    spec_rows: window.PaperStore.specRows || []
                })
            });
            const data = await res.json();
            if (data.status === 'success') {
                if (window.showToast) window.showToast('预设已保存', 'success');
                window.loadPersonalTemplates();
            } else {
                if (window.showToast) window.showToast(data.message || '保存失败', 'warning');
            }
        } catch (e) {
            if (window.showToast) window.showToast('保存请求失败', 'warning');
        }
    };

    // 模板编辑器（新增 / 编辑内置 / 编辑个人）
    window.openTemplateEditor = function (kind, keyOrId) {
        let editing = null;
        if (kind === 'builtin') {
            const t = BUILTIN_TEMPLATES.find(x => x.key === keyOrId);
            if (t) editing = { name: t.name, meta: Object.assign({}, t.meta), specRows: JSON.parse(JSON.stringify(t.specRows)) };
        } else if (kind === 'personal') {
            // personal 编辑器在列表加载后绑定，这里通过全局缓存取
            editing = window.__personalTemplateCache ? window.__personalTemplateCache.find(t => t.id === keyOrId) : null;
            if (editing) {
                editing = { name: editing.name, meta: { paper_type: editing.paper_type, total_score_target: editing.total_score_target, solution_space_default: '7.0' }, specRows: JSON.parse(JSON.stringify(editing.spec_rows || [])) };
            }
        }

        const body = document.getElementById('templateModalBody');
        if (!body) return;
        const ed = editing || { name: '', meta: { paper_type: 'exam', total_score_target: 100, solution_space_default: '7.0' }, specRows: [] };
        if (!ed.specRows || !ed.specRows.length) ed.specRows = [{ knowledge: '', question_type: '', difficulty: '', solve_method: '', count: 1 }];

        const ptypeOpts = [
            { v: 'exam_19', l: '19题高考卷(含答题卡)' },
            { v: 'exam', l: '常规试卷' },
            { v: 'quiz', l: '日常小练' }
        ].map(o => `<option value="${o.v}" ${o.v === ed.meta.paper_type ? 'selected' : ''}>${o.l}</option>`).join('');
        const spaceOpts = [
            { v: '0.0', l: '不留白' }, { v: '3.0', l: '紧凑 3cm' }, { v: '7.0', l: '标准 7cm' }
        ].map(o => `<option value="${o.v}" ${parseFloat(o.v) === parseFloat(ed.meta.solution_space_default || '7.0') ? 'selected' : ''}>${o.l}</option>`).join('');

        body.innerHTML = `
            <div class="space-y-4" id="tmplEditorWrap">
                <div class="flex items-center gap-2">
                    <button onclick="window.renderTemplateList()" class="text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"><i class="fa-solid fa-arrow-left mr-1"></i>返回</button>
                    <div class="text-sm font-bold text-slate-700 dark:text-slate-200">${editing ? '编辑模板' : '新增预设'}</div>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-[11px] font-semibold text-slate-500 mb-1">模板名称</label>
                        <input id="tmplEdName" value="${escapeHtml(ed.name)}" class="w-full px-2 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800" placeholder="如：高一函数周测" />
                    </div>
                    <div>
                        <label class="block text-[11px] font-semibold text-slate-500 mb-1">试卷版式</label>
                        <select id="tmplEdType" class="w-full px-2 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">${ptypeOpts}</select>
                    </div>
                    <div>
                        <label class="block text-[11px] font-semibold text-slate-500 mb-1">目标总分</label>
                        <input id="tmplEdScore" type="number" min="0" max="300" value="${escapeHtml(String(ed.meta.total_score_target || ''))}" class="w-full px-2 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800" />
                    </div>
                    <div>
                        <label class="block text-[11px] font-semibold text-slate-500 mb-1">默认留白</label>
                        <select id="tmplEdSpace" class="w-full px-2 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">${spaceOpts}</select>
                    </div>
                </div>
                <div>
                    <div class="flex items-center justify-between mb-2">
                        <label class="text-[11px] font-semibold text-slate-500">细目表（可选，应用时不自动抽题）</label>
                        <button onclick="window.tmplEditorAddRow()" class="text-[10px] px-2 py-0.5 rounded-lg bg-brand-50 text-brand-700 font-semibold border border-brand-200/60 hover:bg-brand-100 dark:bg-brand-900/40 dark:text-brand-200 dark:border-brand-900">+ 加一行</button>
                    </div>
                    <div id="tmplEdSpecRows" class="space-y-1.5"></div>
                </div>
                <div class="flex items-center justify-end gap-3 pt-1">
                    <button onclick="window.renderTemplateList()" class="px-4 py-1.5 rounded-xl bg-slate-200 text-slate-700 hover:bg-slate-300 font-medium dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700">取消</button>
                    <button onclick="window.saveTemplateEditor()" class="px-4 py-1.5 rounded-xl bg-emerald-600 text-white font-semibold hover:bg-emerald-700 active:scale-95 transition-all">保存预设</button>
                </div>
                <div id="tmplEdMsg" class="text-[10px] text-slate-500"></div>
            </div>
        `;
        // 暂存编辑器草稿
        window.__tmplEditorDraft = ed;
        window.renderTmplEditorRows();
    };

    window.tmplEditorAddRow = function () {
        if (!window.__tmplEditorDraft) window.__tmplEditorDraft = { specRows: [] };
        if (!window.__tmplEditorDraft.specRows) window.__tmplEditorDraft.specRows = [];
        window.__tmplEditorDraft.specRows.push({ knowledge: '', question_type: '', difficulty: '', solve_method: '', count: 1 });
        window.renderTmplEditorRows();
    };

    function renderTmplEditorRows() {
        const wrap = document.getElementById('tmplEdSpecRows');
        if (!wrap || !window.__tmplEditorDraft) return;
        const rows = window.__tmplEditorDraft.specRows;
        const typeOpts = SPEC_QTYPES;
        const diffOpts = SPEC_DIFFS;
        wrap.innerHTML = rows.map((r, i) => `
            <div class="flex flex-wrap items-center gap-1.5" data-te-row="${i}">
                <input data-te="knowledge" data-idx="${i}" value="${escapeHtml(r.knowledge)}" placeholder="考点(可空)" class="w-24 px-1.5 py-1 text-[10px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800" />
                ${buildSpecSelect(typeOpts, r.question_type, 'te-type')}
                ${buildSpecSelect(diffOpts, r.difficulty, 'te-diff')}
                <input data-te="solve_method" data-idx="${i}" value="${escapeHtml(r.solve_method)}" placeholder="解法(可空)" class="w-20 px-1.5 py-1 text-[10px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800" />
                <input data-te="count" data-idx="${i}" type="number" min="1" max="20" value="${r.count}" class="w-12 px-1.5 py-1 text-[10px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800" title="题量" />
                <button onclick="window.tmplEditorRemoveRow(${i})" class="px-1.5 py-1 text-[10px] rounded-lg text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40"><i class="fa-solid fa-trash-can"></i></button>
            </div>
        `).join('');
        wrap.querySelectorAll('[data-te]').forEach(inp => {
            inp.addEventListener('input', (e) => {
                const idx = parseInt(e.target.dataset.idx, 10);
                const key = e.target.dataset.te;
                if (window.__tmplEditorDraft.specRows[idx]) window.__tmplEditorDraft.specRows[idx][key] = e.target.value;
            });
        });
        wrap.querySelectorAll('.te-type, .te-diff').forEach(sel => {
            sel.addEventListener('change', (e) => {
                const idx = Array.from(wrap.querySelectorAll('[data-te-row]')).indexOf(sel.closest('[data-te-row]'));
                const key = sel.classList.contains('te-type') ? 'question_type' : 'difficulty';
                if (window.__tmplEditorDraft.specRows[idx]) window.__tmplEditorDraft.specRows[idx][key] = sel.value;
            });
        });
    }

    window.tmplEditorRemoveRow = function (i) {
        if (!window.__tmplEditorDraft || !window.__tmplEditorDraft.specRows) return;
        window.__tmplEditorDraft.specRows.splice(i, 1);
        if (window.__tmplEditorDraft.specRows.length === 0) {
            window.__tmplEditorDraft.specRows.push({ knowledge: '', question_type: '', difficulty: '', solve_method: '', count: 1 });
        }
        window.renderTmplEditorRows();
    };

    window.saveTemplateEditor = async function () {
        const draft = window.__tmplEditorDraft;
        if (!draft) return;
        const name = (document.getElementById('tmplEdName') || {}).value || '';
        const paper_type = (document.getElementById('tmplEdType') || {}).value || 'exam';
        const total_score_target = parseInt((document.getElementById('tmplEdScore') || {}).value || '0', 10) || 0;
        const solution_space_default = (document.getElementById('tmplEdSpace') || {}).value || '7.0';
        if (!name.trim()) {
            const msg = document.getElementById('tmplEdMsg');
            if (msg) msg.innerHTML = '<span class="text-rose-600">请填写模板名称</span>';
            return;
        }
        const spec_rows = (draft.specRows || []).filter(r => r && (r.knowledge.trim() || r.question_type || r.difficulty || r.solve_method.trim() || r.count))
            .map(r => ({ knowledge: r.knowledge, question_type: r.question_type, difficulty: r.difficulty, solve_method: r.solve_method, count: parseInt(r.count, 10) || 1 }));
        try {
            const res = await fetch('/api/paper/save-template', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    meta: { paper_type, total_score_target, solution_space_default },
                    spec_rows: spec_rows
                })
            });
            const data = await res.json();
            if (data.status === 'success') {
                if (window.showToast) window.showToast('预设已保存', 'success');
                window.renderTemplateList();
            } else {
                const msg = document.getElementById('tmplEdMsg');
                if (msg) msg.innerHTML = `<span class="text-rose-600">${escapeHtml(data.message || '保存失败')}</span>`;
            }
        } catch (e) {
            const msg = document.getElementById('tmplEdMsg');
            if (msg) msg.innerHTML = '<span class="text-rose-600">保存请求失败</span>';
        }
    };

    window.loadPersonalTemplates = async function () {
        const listEl = document.getElementById('personalTemplateList');
        if (!listEl) return;
        try {
            const res = await fetch('/api/paper/templates');
            const data = await res.json();
            const tpls = data.data || [];
            window.__personalTemplateCache = tpls;
            if (tpls.length === 0) {
                listEl.innerHTML = '<div class="text-[10px] text-slate-400">暂无个人预设，点右下「新增预设」创建，或套用内置模板后「存为预设」。</div>';
                return;
            }
            listEl.innerHTML = tpls.map(t => `
                <div class="flex items-center justify-between bg-slate-50 dark:bg-slate-800/60 rounded-xl px-3 py-2.5">
                    <button onclick="window.applyTemplateFlow('personal', ${t.id})" class="flex-1 text-left min-w-0">
                        <div class="text-xs font-bold text-slate-700 dark:text-slate-200 truncate">${escapeHtml(t.name)}</div>
                        <div class="text-[10px] text-slate-400">${escapeHtml(t.paper_type === 'exam_19' ? '高考卷' : t.paper_type === 'quiz' ? '小练' : '常规')} · ${escapeHtml(String(t.total_score_target || ''))}分${t.spec_rows && t.spec_rows.length ? ' · 含细目表' : ''}</div>
                    </button>
                    <div class="flex items-center gap-1.5 shrink-0 ml-2">
                        <button onclick="window.openTemplateEditor('personal', ${t.id})" class="px-2 py-1 rounded-lg text-[10px] font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700" title="编辑">编辑</button>
                        <button onclick="window.deletePersonalTemplate(${t.id})" class="px-2 py-1 rounded-lg text-[10px] font-semibold text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40" title="删除">删除</button>
                    </div>
                </div>
            `).join('');
        } catch (e) {
            listEl.innerHTML = '<div class="text-[10px] text-rose-500">加载预设失败</div>';
        }
    };

    window.applyPersonalTemplate = async function (id) {
        window.applyTemplateFlow('personal', id);
    };

    window.deletePersonalTemplate = async function (id) {
        if (!confirm('确定删除该预设？')) return;
        try {
            const res = await fetch(`/api/paper/template/${id}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.status === 'success') {
                window.loadPersonalTemplates();
                if (window.showToast) window.showToast('预设已删除', 'info');
            }
        } catch (e) { }
    };

    // ---- 细目表组卷 (Spec-based batch selection) ----
    const SPEC_QTYPES = [
        { value: '', label: '不限题型' },
        { value: 'single_choice', label: '单选' },
        { value: 'multi_choice', label: '多选' },
        { value: 'fill_in_blank', label: '填空' },
        { value: 'detailed_answer', label: '解答' }
    ];
    const SPEC_DIFFS = [
        { value: '', label: '不限难度' },
        { value: 'easy_error', label: '易错题' },
        { value: 'normal', label: '常规题' },
        { value: 'challenge', label: '挑战题' },
        { value: 'qiangji', label: '强基题' }
    ];

    function buildSpecSelect(options, selectedVal, cls) {
        return `<select class="${cls} px-1.5 py-1 text-[10px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 focus:ring-1 focus:ring-brand-500 focus:outline-none">
            ${options.map(o => `<option value="${o.value}" ${o.value === selectedVal ? 'selected' : ''}>${o.label}</option>`).join('')}
        </select>`;
    }

    // 渲染细目表行到指定容器（供弹窗复用）
    function renderSpecRowsInto(container) {
        if (!window.PaperStore.specRows) {
            window.PaperStore.specRows = [{ knowledge: '', question_type: '', difficulty: '', solve_method: '', count: 1 }];
        }
        const rows = window.PaperStore.specRows;
        const typeOpts = SPEC_QTYPES;
        const diffOpts = SPEC_DIFFS;

        const rowHtml = rows.map((r, i) => `
            <div class="flex flex-wrap items-center gap-1.5 mb-1.5" data-spec-row="${i}">
                <input data-spec="knowledge" data-idx="${i}" value="${escapeHtml(r.knowledge)}" placeholder="考点(可空/多值)" list="specKnowledgeList" class="spec-input w-24 px-1.5 py-1 text-[10px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 focus:ring-1 focus:ring-brand-500 focus:outline-none" />
                ${buildSpecSelect(typeOpts, r.question_type, 'spec-type')}
                ${buildSpecSelect(diffOpts, r.difficulty, 'spec-diff')}
                <input data-spec="solve_method" data-idx="${i}" value="${escapeHtml(r.solve_method)}" placeholder="解法(可空)" class="spec-input w-20 px-1.5 py-1 text-[10px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 focus:ring-1 focus:ring-brand-500 focus:outline-none" />
                <input data-spec="count" data-idx="${i}" type="number" min="1" max="20" value="${r.count}" class="spec-input w-12 px-1.5 py-1 text-[10px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 focus:ring-1 focus:ring-brand-500 focus:outline-none" title="题量" />
                <button onclick="window.removeSpecRow(${i})" class="px-1.5 py-1 text-[10px] rounded-lg text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40" title="删除该行">
                    <i class="fa-solid fa-trash-can"></i>
                </button>
            </div>
        `).join('');

        const body = `
            <datalist id="specKnowledgeList"></datalist>
            <div class="text-[10px] text-slate-400 -mt-1 mb-2">考点/解法支持多值（逗号分隔，OR 匹配）</div>
            <div id="specRowsContainer">${rowHtml}</div>
        `;
        container.innerHTML = body;

        // bind inputs
        container.querySelectorAll('.spec-input').forEach(inp => {
            inp.addEventListener('input', (e) => {
                const idx = parseInt(e.target.dataset.idx, 10);
                const key = e.target.dataset.spec;
                if (window.PaperStore.specRows[idx]) {
                    window.PaperStore.specRows[idx][key] = e.target.value;
                }
            });
        });
        container.querySelectorAll('.spec-type, .spec-diff').forEach(sel => {
            sel.addEventListener('change', (e) => {
                const idx = Array.from(container.querySelectorAll('[data-spec-row]')).indexOf(sel.closest('[data-spec-row]'));
                const key = sel.classList.contains('spec-type') ? 'question_type' : 'difficulty';
                if (window.PaperStore.specRows[idx]) {
                    window.PaperStore.specRows[idx][key] = sel.value;
                }
            });
        });

        window.loadSpecTagOptions();
    }

    // 打开细目表组卷弹窗
    window.openSpecModal = function () {
        let modal = document.getElementById('specModal');
        if (modal) {
            window.MathBankModal.close(modal);
            modal.remove();
        }
        modal = document.createElement('div');
        modal.id = 'specModal';
        modal.className = 'fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-md flex items-center justify-center p-4 animate-in fade-in duration-200';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'specModalTitle');
        modal.innerHTML = `
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden font-sans">
                <div class="px-6 py-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-slate-50/50 dark:bg-slate-800/50">
                    <div class="flex items-center space-x-2.5">
                        <div class="w-9 h-9 rounded-2xl bg-brand-500/10 text-brand-600 dark:text-brand-400 flex items-center justify-center font-bold text-lg">
                            <i class="fa-solid fa-table-list"></i>
                        </div>
                        <div>
                            <h3 id="specModalTitle" class="font-bold text-slate-800 dark:text-slate-100 text-base">细目表组卷</h3>
                            <p class="text-xs text-slate-400">按题型/难度/知识点/解法设定每组抽题条件，一键抽取入篮</p>
                        </div>
                    </div>
                    <button type="button" onclick="closeSpecModal()" aria-label="关闭" class="w-8 h-8 rounded-full hover:bg-slate-200/60 dark:hover:bg-slate-700 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 flex items-center justify-center transition-colors">
                        <i class="fa-solid fa-xmark text-sm"></i>
                    </button>
                </div>
                <div class="p-6 overflow-y-auto flex-1" id="specModalBody"></div>
                <div class="px-6 py-3.5 border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-800/50 flex items-center justify-between gap-3">
                    <button onclick="window.addSpecRow()" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-brand-50 text-brand-700 border border-brand-200/60 hover:bg-brand-100 dark:bg-brand-900/40 dark:text-brand-200 dark:border-brand-900 flex items-center space-x-1">
                        <i class="fa-solid fa-plus"></i><span>加一行</span>
                    </button>
                    <div class="flex items-center gap-3">
                        <span id="specResultMsg" class="text-[10px] text-slate-500"></span>
                        <button onclick="window.runSpecBatchSelect()" class="px-4 py-1.5 rounded-xl text-xs font-semibold bg-brand-600 text-white shadow-sm hover:bg-brand-700 active:scale-95 transition-all flex items-center space-x-1.5">
                            <i class="fa-solid fa-wand-magic-sparkles"></i><span>按细目表抽题入篮</span>
                        </button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        window.MathBankModal.open(modal, { onEscape: window.closeSpecModal });
        renderSpecRowsInto(document.getElementById('specModalBody'));
    };

    window.closeSpecModal = function () {
        const modal = document.getElementById('specModal');
        if (modal) {
            window.MathBankModal.close(modal);
            modal.remove();
        }
    };


    window.addSpecRow = function () {
        if (!window.PaperStore.specRows) window.PaperStore.specRows = [];
        window.PaperStore.specRows.push({ knowledge: '', question_type: '', difficulty: '', solve_method: '', count: 1 });
        const body = document.getElementById('specModalBody');
        if (body) renderSpecRowsInto(body);
    };

    window.removeSpecRow = function (i) {
        if (!window.PaperStore.specRows) return;
        window.PaperStore.specRows.splice(i, 1);
        if (window.PaperStore.specRows.length === 0) {
            window.PaperStore.specRows.push({ knowledge: '', question_type: '', difficulty: '', solve_method: '', count: 1 });
        }
        const body = document.getElementById('specModalBody');
        if (body) renderSpecRowsInto(body);
    };

    window.loadSpecTagOptions = async function () {
        const dl = document.getElementById('specKnowledgeList');
        if (!dl) return;
        try {
            const res = await fetch('/api/tag-options');
            const data = await res.json();
            const knows = (data.knowledge_list || []).map(k => `<option value="${escapeHtml(k)}">`).join('');
            const solves = (data.solve_method || []).map(s => `<option value="${escapeHtml(s)}">`).join('');
            dl.innerHTML = knows + solves;
        } catch (e) { }
    };

    window.runSpecBatchSelect = async function () {
        const rows = (window.PaperStore.specRows || []).filter(r => r && (r.knowledge.trim() || r.question_type || r.difficulty || r.solve_method.trim()));
        if (rows.length === 0) {
            if (window.showToast) window.showToast('请至少填写一行考点或题型', 'warning');
            return;
        }
        const msgEl = document.getElementById('specResultMsg');
        if (msgEl) msgEl.innerHTML = '<span class="text-brand-600"><i class="fa-solid fa-spinner fa-spin mr-1"></i>正在按细目表抽题…</span>';
        try {
            const res = await fetch('/api/paper/batch-select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ rows: rows })
            });
            const data = await res.json();
            if (data.status !== 'success') {
                if (msgEl) msgEl.innerHTML = `<span class="text-rose-600">${escapeHtml(data.message || '抽题失败')}</span>`;
                return;
            }
            // 回填 cart（已去重，避免与现有 cart 重复）
            let added = 0;
            (data.questions || []).forEach(q => {
                if (!window.isInCart(q.id)) {
                    window.addToCart(q.id, q.question_type === 'detailed_answer' ? 12 : 5);
                    added++;
                }
            });
            const gapTxt = data.total_gap > 0
                ? `<span class="text-amber-600 font-semibold">缺口 ${data.total_gap} 道</span>（已选 ${data.selected_count}/${data.total_need}）`
                : `<span class="text-emerald-600 font-semibold">已凑齐 ${data.selected_count}/${data.total_need} 道</span>`;
            if (msgEl) msgEl.innerHTML = `<span class="text-slate-600">${gapTxt}。新增入篮 ${added} 道。可点右侧「组卷体检」查看分布。</span>`;
            if (window.showToast) window.showToast(`细目表抽题完成：入篮 ${added} 道，缺口 ${data.total_gap} 道`, data.total_gap > 0 ? 'warning' : 'success');
            window.renderPaperCanvas();
        } catch (e) {
            if (msgEl) msgEl.innerHTML = `<span class="text-rose-600">请求失败：${escapeHtml(e.message)}</span>`;
        }
    };



    // Render Part 2: Config & 3-Level Cascade Filter Section
    function renderPart2FilterSection() {
        const meta = window.PaperStore.meta;
        const f = window.PaperStore.filters;
        const container = document.getElementById('paperFilterSection');
        if (!container) return;

        // 章节树取「勾选学科的并集」：物化的教材树与数学不是同一套，混科组卷时
        // 只放当前题库 Tab 的那棵树，会把另一科的章节整个挡在下拉外面。
        const pickedSubjectsForTree = paperFilterSubjects();
        const tree = mergedSubjectTree(pickedSubjectsForTree.length ? pickedSubjectsForTree : SUBJECT_ORDER);
        const metadata = window.systemMetadata || {};

        // 学科 chips：可多选，但至少留一科（全不勾等于没题可抽，比空列表更容易让人懵）。
        const subjectChipsHtml = SUBJECT_ORDER.map(s => {
            const on = pickedSubjectsForTree.indexOf(s) >= 0;
            return `<button type="button" onclick="window.togglePaperFilterSubject('${s}')" aria-pressed="${on ? 'true' : 'false'}"
                        class="px-2 py-0.5 rounded-lg text-xs font-semibold border transition-all ${on
                    ? 'bg-brand-50 text-brand-600 border-brand-200/70 dark:bg-brand-900/30 dark:text-brand-200 dark:border-brand-900'
                    : 'bg-white text-slate-400 border-slate-200 dark:bg-slate-800 dark:text-slate-500 dark:border-slate-700'}">${escapeHtml(SUBJECT_LABELS[s])}</button>`;
        }).join('');

        // 1. Build Compulsory Book options
        let bookOptions = `<option value="">-- 选择学段 --</option>`;
        Object.keys(tree).forEach(b => {
            bookOptions += `<option value="${escapeHtml(b)}" ${f.compulsory === b ? 'selected' : ''}>${escapeHtml(b)}</option>`;
        });

        // 2. Build Chapter options (Level 2)
        let chapterOptions = `<option value="">-- 先选学段 --</option>`;
        let isChapterDisabled = true;
        if (f.compulsory && tree[f.compulsory]) {
            isChapterDisabled = false;
            chapterOptions = `<option value="">-- 所有章节 --</option>`;
            Object.keys(tree[f.compulsory]).forEach(ch => {
                chapterOptions += `<option value="${escapeHtml(ch)}" ${f.chapter === ch ? 'selected' : ''}>${escapeHtml(ch)}</option>`;
            });
        }

        // 3. Build Knowledge options (Level 3)
        let knowledgeOptions = `<option value="">-- 先选章节 --</option>`;
        let isKnowledgeDisabled = true;
        if (f.compulsory && f.chapter && tree[f.compulsory] && tree[f.compulsory][f.chapter]) {
            isKnowledgeDisabled = false;
            knowledgeOptions = `<option value="">-- 所有小节/知识点 --</option>`;
            const knowList = tree[f.compulsory][f.chapter];
            if (Array.isArray(knowList)) {
                knowList.forEach(k => {
                    knowledgeOptions += `<option value="${escapeHtml(k)}" ${f.knowledge === k ? 'selected' : ''}>${escapeHtml(k)}</option>`;
                });
            }
        }

        // 4. Build Question Type options
        let qTypeOptions = `<option value="">全部题型</option>`;
        const qTypes = metadata.question_types || [
            { value: 'single_choice', label: '单选题' },
            { value: 'multi_choice', label: '多选题' },
            { value: 'fill_in_blank', label: '填空题' },
            { value: 'detailed_answer', label: '解答题' }
        ];
        qTypes.forEach(t => {
            qTypeOptions += `<option value="${escapeHtml(t.value)}" ${f.question_type === t.value ? 'selected' : ''}>${escapeHtml(t.label)}</option>`;
        });

        // 5. Build Difficulty options
        let diffOptions = `<option value="">全部难度</option>`;
        const difficulties = metadata.difficulties || [
            { value: 'easy_error', label: '易错题' },
            { value: 'normal', label: '常规题' },
            { value: 'challenge', label: '挑战题' },
            { value: 'qiangji', label: '强基题' }
        ];
        difficulties.forEach(d => {
            diffOptions += `<option value="${escapeHtml(d.value)}" ${f.difficulty === d.value ? 'selected' : ''}>${escapeHtml(d.label)}</option>`;
        });

        container.innerHTML = `
            <div class="space-y-2 bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 p-3 rounded-2xl shadow-sm">
                <!-- 主标题/副标题/试卷类型：已迁移到右侧试卷预览区直接点击编辑（见 canvas-meta-title / canvas-meta-subtitle），左侧不再重复。 -->

                <!-- 学科行：一份卷子可以跨学科，所以这里能同时勾多科 -->
                <div class="flex items-center flex-wrap gap-1.5 pb-0.5">
                    <span class="text-xs font-semibold text-slate-500 dark:text-slate-400 mr-0.5">学科</span>
                    ${subjectChipsHtml}
                </div>

                <!-- Middle Row 1: 3-Level Cascade Curriculum Dropdowns (学段 -> 章节 -> 小节/知识点) -->
                <div class="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1.5 border-t border-slate-100 dark:border-slate-800/60">
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">学段</label>
                        <select id="paperFilterCompulsory" onchange="onPaperFilterChange('compulsory', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            ${bookOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">章节</label>
                        <select id="paperFilterChapter" onchange="onPaperFilterChange('chapter', this.value)" ${isChapterDisabled ? 'disabled' : ''}
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg disabled:opacity-50">
                            ${chapterOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">小节 / 知识点</label>
                        <select id="paperFilterKnowledge" onchange="onPaperFilterChange('knowledge', this.value)" ${isKnowledgeDisabled ? 'disabled' : ''}
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg disabled:opacity-50">
                            ${knowledgeOptions}
                        </select>
                    </div>
                </div>

                <!-- Middle Row 2: Question Type, Difficulty & Search Input -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">题型</label>
                        <select id="paperFilterType" onchange="onPaperFilterChange('question_type', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            ${qTypeOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">难度</label>
                        <select id="paperFilterDifficulty" onchange="onPaperFilterChange('difficulty', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            ${diffOptions}
                        </select>
                    </div>
                    <div class="col-span-2">
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">来源 / 自定义标签 / 关键词</label>
                        <div class="relative">
                            <i class="fa-solid fa-magnifying-glass text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2 text-xs"></i>
                            <input type="text" id="paperFilterKeyword" value="${escapeHtml(f.keyword)}"
                                oninput="onPaperFilterChange('keyword', this.value)"
                                class="glass-input w-full pl-7 pr-2.5 py-1 text-xs rounded-lg" placeholder="搜索题干内容 / 来源 / 标签 / 批注...">
                        </div>
                    </div>
                </div>

                <!-- Bottom Row: AI Prompt Selection Bar -->
                <div class="pt-1.5 border-t border-slate-100 dark:border-slate-800/60 flex items-center space-x-2">
                    <div class="relative flex-1">
                        <i class="fa-solid fa-wand-magic-sparkles text-brand-500 absolute left-2.5 top-1/2 -translate-y-1/2 text-[10px]"></i>
                        <input type="text" id="paperAiPromptInput" 
                            class="glass-input w-full pl-7 pr-2.5 py-1 text-[10px] rounded-lg border-brand-200/80"
                            placeholder="智能一键抽卷：例如“帮我抽 5 道难度中等的函数选择题”"
                            onkeypress="if(event.key==='Enter') triggerAiPaperSelect()">
                    </div>
                    <button onclick="triggerAiPaperSelect()" class="glass-btn-primary h-[28px] px-3 rounded-lg text-[10px] font-semibold flex items-center space-x-1 shrink-0">
                        <span>智能抽取</span>
                    </button>
                    <button onclick="window.openSpecModal()" class="h-[28px] px-2.5 rounded-lg text-[10px] font-semibold border border-brand-200/80 bg-white text-brand-700 hover:bg-brand-50 transition-all flex items-center space-x-1 shrink-0 dark:bg-slate-800 dark:border-brand-900 dark:text-brand-200 dark:hover:bg-brand-900/40" title="按细目表（题型/难度/知识点）批量抽题">
                        <i class="fa-solid fa-table-list text-[10px]"></i>
                        <span>细目表组卷</span>
                    </button>
                    <select id="paperAiFreshPriority" title="避重策略：优先未用过(鲜活) 或 优先高频旧题"
                        class="h-[28px] px-1.5 rounded-lg text-[10px] border border-brand-200/80 bg-brand-50/60 text-brand-900 font-semibold focus:ring-1 focus:ring-brand-500 focus:outline-none dark:bg-brand-900/50 dark:border-brand-900 dark:text-brand-200 shrink-0">
                        <option value="">避重:自动</option>
                        <option value="true">优先未用过</option>
                        <option value="false">优先旧题</option>
                    </select>
                </div>
            </div>
        `;
    }

    // 学科 chips 开关。至少保留一科；切换后清掉学段/章节/小节 —— 那些选项目前是
    // 按学科拼出来的，留着会让「学段停在物理必修一、库里却只有数学题」这种空结果出现。
    window.togglePaperFilterSubject = function (subject) {
        const key = String(subject || '').toLowerCase();
        if (SUBJECT_ORDER.indexOf(key) < 0) return;
        const current = paperFilterSubjects();
        let next = current.indexOf(key) >= 0
            ? current.filter(s => s !== key)
            : SUBJECT_ORDER.filter(s => current.indexOf(s) >= 0 || s === key);
        if (next.length === 0) {
            if (window.showToast) window.showToast('至少要保留一个学科', 'warning');
            next = current.slice();
        }
        window.PaperStore.filters.subjects = next;
        window.PaperStore.filters.compulsory = '';
        window.PaperStore.filters.chapter = '';
        window.PaperStore.filters.knowledge = '';
        renderPart2FilterSection();
        fetchBankQuestions().then(() => {
            renderPart3QuestionStream({ resetScroll: true });
        });
    };

    let filterDebounceTimer = null;
    window.onPaperFilterChange = function (key, value) {
        window.PaperStore.filters[key] = value;
        
        // Handle cascade resets
        if (key === 'compulsory') {
            window.PaperStore.filters.chapter = '';
            window.PaperStore.filters.knowledge = '';
            renderPart2FilterSection();
        } else if (key === 'chapter') {
            window.PaperStore.filters.knowledge = '';
            renderPart2FilterSection();
        }

        if (key === 'keyword') {
            clearTimeout(filterDebounceTimer);
            filterDebounceTimer = setTimeout(async () => {
                await fetchBankQuestions();
                renderPart3QuestionStream({ resetScroll: true });
            }, 300);
        } else {
            fetchBankQuestions().then(() => {
                renderPart3QuestionStream({ resetScroll: true });
            });
        }
    };

    function syncCanvasHeaderMeta(key, value) {
        const cleanVal = (value || '').trim();
        if (key === 'title') {
            const nodes = document.querySelectorAll('.canvas-meta-title');
            nodes.forEach(node => {
                if (node !== document.activeElement) {
                    if (!cleanVal) {
                        node.innerHTML = '';
                    } else if (node.innerText !== value) {
                        node.innerText = value;
                    }
                } else if (!cleanVal && node.innerHTML !== '') {
                    if (node.innerText.trim() === '') node.innerHTML = '';
                }
            });
        } else if (key === 'subtitle' || key === 'subject_line') {
            const nodes = document.querySelectorAll(key === 'subtitle' ? '.canvas-meta-subtitle' : '.canvas-meta-subject');
            nodes.forEach(node => {
                if (node !== document.activeElement) {
                    if (!cleanVal) {
                        node.innerHTML = '';
                    } else if (node.innerText !== value) {
                        node.innerText = value;
                    }
                } else if (!cleanVal && node.innerHTML !== '') {
                    if (node.innerText.trim() === '') node.innerHTML = '';
                }
            });
        }
    }

    window.updatePaperMeta = function (key, value) {
        window.PaperStore.meta[key] = value;
        if (key === 'subject_line') {
            // 用户亲手改了抬头学科行 → 之后自动跟随卷内学科的逻辑永久退避
            window.PaperStore.meta.subject_line_custom = true;
        }
        if (key === 'paper_type') {
            const newDefault = value === 'exam_19' ? '0.0' : '7.0';
            window.PaperStore.meta.solution_space_default = newDefault;
            window.PaperStore.cart.forEach(item => {
                item.solution_space = newDefault;
            });
            saveCartToStorage();
            renderPart2FilterSection();
        }
        saveMetaToStorage();

        if (key === 'title' || key === 'subtitle' || key === 'subject_line') {
            syncCanvasHeaderMeta(key, value);
        } else {
            window.renderPaperCanvas();
        }
    };

    // 考试用时没有输入框，直接在卷面那行上点改 —— 与「点击标题直接改」同一套交互。
    window.editPaperExamDuration = function () {
        const current = examDurationMinutes(window.PaperStore.meta);
        const input = prompt('考试用时（分钟）：', String(current));
        if (input === null) return;
        const value = parseInt(input, 10);
        if (isNaN(value) || value <= 0 || value > 600) {
            if (window.showToast) window.showToast('考试用时需要是 1-600 之间的整数', 'error');
            return;
        }
        window.updatePaperMeta('exam_duration', value);
        if (window.showToast) window.showToast('考试用时已改为 ' + value + ' 分钟', 'info');
    };

    window.triggerAiPaperSelect = async function () {
        const input = document.getElementById('paperAiPromptInput');
        const btn = document.querySelector('button[onclick="triggerAiPaperSelect()"]');
        if (!input || !input.value.trim()) {
            if (window.showToast) window.showToast('请先输入智能抽卷要求', 'warning');
            return;
        }
        const promptText = input.value.trim();
        const origBtnHtml = btn ? btn.innerHTML : '';

        try {
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = `<i class="fa-solid fa-brain fa-spin text-xs"></i><span>AI 思考组卷中...</span>`;
            }
            if (window.showToast) window.showToast('正在调用 AI 大模型分析需求并遴选最佳题目...', 'info');
            const f = window.PaperStore.filters;
            const res = await fetch('/api/paper/ai-select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt: promptText,
                    limit: 5,
                    compulsory: f.compulsory,
                    chapter: f.chapter,
                    knowledge: f.knowledge,
                    knowledge_list: f.knowledge_list || f.knowledge || '',
                    solve_method: f.solve_method || '',
                    question_type: f.question_type,
                    difficulty: f.difficulty,
                    fresh_priority: (document.getElementById('paperAiFreshPriority') || {}).value || ''
                })
            });
            const data = await res.json();
            if (data.status === 'success' && Array.isArray(data.data)) {
                let addedCount = 0;
                data.data.forEach(q => {
                    if (!window.isInCart(q.id)) {
                        window.PaperStore.cart.push({ id: q.id, score: q.question_type === 'detailed_answer' ? 12 : 5 });
                        window.PaperStore.questionsMap[q.id] = q;
                        addedCount++;
                    }
                });
                saveCartToStorage();
                renderPart3QuestionStream();
                window.renderPaperCanvas();
                if (window.showToast) {
                    const engineLabel = data.fallback ? '算法' : (data.model_used || 'AI 大模型');
                    window.showToast(`AI (${engineLabel}) 成功挑选并加入了 ${addedCount} 道试题`, 'success');
                }
            } else {
                if (window.showToast) window.showToast(data.message || '抽选试题无结果', 'warning');
            }
        } catch (e) {
            if (window.showToast) window.showToast('AI 抽题请求异常', 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = origBtnHtml;
            }
        }
    };

    // Switch Part 3 Tab ('all' or 'selected')
    window.switchPaperStreamTab = function (tabName) {
        window.PaperStore.filters.tab = tabName;
        renderPart3QuestionStream({ resetScroll: true });
    };

    // ============================================================
    // Part 3: 虚拟滚动渲染（高度自适应，支持上万题）
    // ============================================================
    // 2026-09-19 重写：以前是「每卡固定 264px」，但卡片容器不裁剪、content 的
    // flex-1/min-h-0 在非 flex 父级下全部失效，内容一超高就整块溢出到下一张卡上，
    // 且按绘制顺序**上一张卡的插图会盖住下一张卡的「加入试卷」按钮**
    // （2026-09-18 用户截图实测：#1406 的 v-t 图正压在 #1405 的按钮行上）。
    // 现在改为：卡片自然高度 + 渲染后实测回填 + 前缀和定位，装得下多少算多高。
    const PAPER_ITEM_ESTIMATE = 264; // 未实测卡片的高度估计(px)，只影响滚动条手感
    const PAPER_ITEM_GAP = 16;       // 卡片间距(px)，落在每张卡的 margin-bottom 上
    const PAPER_OVERSCAN = 4;        // 视口上下额外渲染条数

    const paperVirtual = {
        displayList: [],
        bound: false,
        rafPending: false,
        heights: new Map(),          // q.id -> 实测卡高（不含间距），跨过滤/切页复用
        resizeObs: null,
        rendered: new Map(),         // q.id -> 视口里那张卡的元素（复用就不必重建）
        lastStart: -1,               // 上一轮渲染的窗口；未变则滚动帧里不做任何 DOM 工作
        lastEnd: -1,
        lastTranslate: null,
        selfScrollTop: null,         // 程序改 scrollTop 时的期望值，用来识别「自己派发的 scroll」
        // 滚动稳定性观测点：updates = 被调用次数，domSyncs = 真的动了 DOM 的次数。
        // 健康状态下连续滚动时 updates 逐帧增长而 domSyncs 几乎不动。
        stats: { updates: 0, domSyncs: 0 }
    };

    /** 一张卡占的槽高 = 卡高(实测优先，未实测用估计) + 间距 */
    function paperSlotHeight(q) {
        const h = q ? paperVirtual.heights.get(q.id) : null;
        return (h || PAPER_ITEM_ESTIMATE) + PAPER_ITEM_GAP;
    }

    /** 前缀和：offsets[i] = 第 i 张卡的顶部 y，offsets[n] = 总高 */
    function paperVirtualOffsets(list) {
        const offsets = [0];
        let acc = 0;
        for (let i = 0; i < list.length; i++) {
            acc += paperSlotHeight(list[i]);
            offsets.push(acc);
        }
        return offsets;
    }

    /** y 落在第几张卡：最大的 i 使 offsets[i] <= y（二分） */
    function paperVirtualIndexAt(offsets, y) {
        if (offsets.length <= 1) return 0;
        let lo = 0, hi = offsets.length - 2;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (offsets[mid] <= y) lo = mid; else hi = mid - 1;
        }
        return lo;
    }

    // 只读数学 + 实测缓存，供 vm 契约测试断言（不改任何行为）
    // 只读数学 + 实测缓存 + 窗口同步观测点，供 vm 契约测试断言（不改任何行为）
    window.PaperVirtualMath = {
        offsets: paperVirtualOffsets,
        indexAt: paperVirtualIndexAt,
        slotHeightOf: paperSlotHeight,
        measuredHeight: function (qid) { return paperVirtual.heights.get(qid); },
        setMeasuredHeight: function (qid, h) { paperVirtual.heights.set(qid, h); },
        resetMeasured: function () { paperVirtual.heights.clear(); },
        update: function () { updatePaperVirtualList(); },
        renderedIds: function () { return Array.from(paperVirtual.rendered.keys()); },
        lastWindow: function () { return { start: paperVirtual.lastStart, end: paperVirtual.lastEnd }; },
        pendingSelfScroll: function () { return paperVirtual.selfScrollTop; },
        stats: function () { return { updates: paperVirtual.stats.updates, domSyncs: paperVirtual.stats.domSyncs }; },
        resetStats: function () { paperVirtual.stats.updates = 0; paperVirtual.stats.domSyncs = 0; },
        ESTIMATE: PAPER_ITEM_ESTIMATE,
        GAP: PAPER_ITEM_GAP
    };

    // 只读契约面：A4 预览分页 / 抬头学科行跟随 / 插图默认排版（vm 测试用）
    window.PaperA4Pagination = {
        paginate: function (blocks) { return paginateA4Blocks(blocks); },
        heightOf: a4BlockHeight
    };
    window.PaperSubjectSync = {
        sync: function () { syncSubjectLineWithCart(); }
    };
    window.PaperFigAlign = {
        get: function (q) { return getQuestionFigAlign(q); }
    };

    function getPaperDisplayList() {
        const cart = window.PaperStore.cart;
        const bankQuestions = window.PaperStore.bankQuestions;
        const currentTab = window.PaperStore.filters.tab || 'all';
        let list = [];
        if (currentTab === 'selected') {
            list = cart.map(item => window.PaperStore.questionsMap[item.id]).filter(Boolean);
        } else {
            list = bankQuestions;
        }
        return list;
    }

    function paperStreamHeaderHtml() {
        const cart = window.PaperStore.cart;
        const bankQuestions = window.PaperStore.bankQuestions;
        const currentTab = window.PaperStore.filters.tab || 'all';
        return `
            <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex items-center space-x-1.5 bg-slate-200/60 p-1 rounded-xl dark:bg-slate-800">
                    <button onclick="switchPaperStreamTab('all')"
                        class="px-3 py-1 rounded-lg text-xs font-bold transition-all ${currentTab === 'all' ? 'bg-white text-brand-600 shadow-sm dark:bg-slate-700 dark:text-brand-200' : 'text-slate-500 hover:text-slate-800 dark:text-slate-400'}">
                        全库试题 (${bankQuestions.length})
                    </button>
                    <button onclick="switchPaperStreamTab('selected')"
                        class="px-3 py-1 rounded-lg text-xs font-bold transition-all ${currentTab === 'selected' ? 'bg-white text-brand-600 shadow-sm dark:bg-slate-700 dark:text-brand-200' : 'text-slate-500 hover:text-slate-800 dark:text-slate-400'}">
                        已选试题 (${cart.length})
                    </button>
                </div>
                <div class="flex flex-wrap items-center justify-end gap-3">
                    ${cart.length > 0 ? `
                        <button type="button" onclick="window.clearCart()" class="text-xs font-medium text-slate-400 hover:text-rose-500 transition-colors flex items-center space-x-1">
                            <i class="fa-solid fa-trash-can text-[10px]"></i>
                            <span>清空卷面 (${cart.length})</span>
                        </button>
                    ` : ''}
                </div>
            </div>
        `;
    }

    // 题卡上的学科角标：只在同时勾了多科时出现。单科浏览时全是同一科，
    // 挂一排重复标签只是噪音，界面与改动前保持一致。
    function paperSubjectBadgeHtml(q) {
        if (paperFilterSubjects().length < 2) return '';
        const label = subjectLabel(q && q.subject);
        if (!label) return '';
        return `<span class="px-2 py-0.5 rounded-lg text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200/70 dark:bg-amber-900/30 dark:text-amber-200 dark:border-amber-900/60" title="所属学科">${escapeHtml(label)}</span>`;
    }

    function renderPaperStreamCard(q, index) {
        const inCart = window.isInCart(q.id);
        const cartItem = window.PaperStore.cart.find(it => it.id === q.id);
        const currentScore = cartItem ? cartItem.score : (q.question_type === 'detailed_answer' ? 12 : 5);
        const qTypeLabel = getQuestionTypeCn(q.question_type);
        const diffTag = getDifficultyBadge(q.difficulty);
        const usageCount = q.usage_count || 0;
        seedPaperAnswerCache(q);
        const answerCached = hasCachedPaperAnswer(q.id);
        const answerText = answerCached ? window.PaperStore.answerCache[q.id] : '';
        const answerAvailabilityKnown = typeof q.has_answer === 'boolean' || answerCached;
        const hasAnswer = Boolean((answerText || '').trim()) || q.has_answer === true || !answerAvailabilityKnown;
        const currentTab = window.PaperStore.filters.tab || 'all';

        const cardBorderClass = inCart
            ? 'border-brand-500 ring-2 ring-brand-500/20 bg-brand-50/10 dark:border-brand-500/60 dark:bg-brand-900/20'
            : 'border-slate-200/80 bg-white/80 dark:bg-slate-800/80 dark:border-slate-700/70';

        const controls = `
            <div class="flex flex-col gap-2 pb-2 mb-2 border-b border-slate-100 sm:flex-row sm:items-center sm:justify-between dark:border-slate-700/60">
                <div class="flex w-full items-center space-x-2 flex-wrap gap-y-1 sm:w-auto">
                    <span class="font-bold text-slate-800 dark:text-slate-100 text-sm">#${escapeHtml(q.seq_num !== undefined ? q.seq_num : q.id)}</span>
                    ${paperSubjectBadgeHtml(q)}
                    <span class="px-2 py-0.5 rounded-lg text-xs font-semibold bg-brand-50 text-brand-600 border border-brand-200/50 dark:bg-brand-900/30 dark:text-brand-200 dark:border-brand-900/50">${escapeHtml(qTypeLabel)}</span>
                    ${diffTag}
                    ${q.category_compulsory ? `<span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300">${escapeHtml(q.category_compulsory)}</span>` : ''}
                    ${q.category_chapter ? `<span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400">${escapeHtml(q.category_chapter)}</span>` : ''}
                    <span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400" title="引用次数">引用 ${escapeHtml(usageCount)} 次</span>
                </div>
                <div class="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
                    <button type="button" onclick="window.openQuestionDetail(${q.id})"
                        class="min-h-[44px] sm:min-h-[32px] px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all flex items-center justify-center space-x-1.5 border-slate-200 bg-white/80 text-slate-600 hover:border-brand-200 hover:bg-brand-50 hover:text-brand-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-brand-700">
                        <i class="fa-solid fa-eye text-[11px]"></i>
                        <span>${hasAnswer ? '查看答案' : '暂无答案'}</span>
                    </button>
                    ${inCart ? `
                        <div class="paper-score-pill flex items-center space-x-1 px-2.5 py-1 rounded-xl">
                            <span class="text-xs font-medium">分值:</span>
                            <input type="number" min="1" max="100" value="${currentScore}" onchange="window.updatePaperQuestionScore(${q.id}, this.value)" class="w-12 text-center text-xs font-bold rounded-lg focus:outline-none">
                            <span class="text-xs font-medium">分</span>
                        </div>
                        ${currentTab === 'selected' ? `
                            <button onclick="window.movePaperQuestion(${index}, 'up')" ${index === 0 ? 'disabled' : ''} class="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-800 disabled:opacity-30 dark:hover:bg-slate-700" title="上移"><i class="fa-solid fa-arrow-up text-xs"></i></button>
                            <button onclick="window.movePaperQuestion(${index}, 'down')" ${index === paperVirtual.displayList.length - 1 ? 'disabled' : ''} class="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-800 disabled:opacity-30 dark:hover:bg-slate-700" title="下移"><i class="fa-solid fa-arrow-down text-xs"></i></button>
                        ` : ''}
                        <button onclick="window.removeFromCart(${q.id})" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-emerald-500 text-white shadow-sm hover:bg-rose-600 active:scale-95 transition-all flex items-center space-x-1" title="点击移出试卷"><i class="fa-solid fa-check text-xs"></i><span>已入卷</span></button>
                    ` : `
                        <button onclick="window.addToCart(${q.id})" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-brand-600 text-white shadow-sm hover:bg-brand-700 active:scale-95 transition-all flex items-center space-x-1"><i class="fa-solid fa-plus text-xs"></i><span>加入试卷</span></button>
                    `}
                </div>
            </div>
        `;

        const content = `
            <div class="flex-1 min-h-0 overflow-hidden text-sm leading-relaxed text-slate-800 dark:text-slate-100 overflow-x-auto select-text" id="paper-q-render-${q.id}">
                ${formatQuestionContentHtml(q.content, q.id, getQuestionFigAlign(q), false, false)}
            </div>
        `;

        return `
            <div class="p-4 rounded-2xl border overflow-hidden ${cardBorderClass}" data-vcard style="margin-bottom:${PAPER_ITEM_GAP}px">
                ${controls}
                ${content}
            </div>
        `;
    }

    // ---- 视口窗口同步（2026-09-20 重写） ----
    // 旧实现滚动帧里无条件 `viewport.innerHTML = html`：哪怕一张卡都没进出视口，也要把
    // 视口内全部卡片重新生成一遍 —— KaTeX 重跑、adaptSingleChoicesGrid 每卡再强制 reflow
    // 几次、题图 <img loading="lazy"> 回落到未加载状态。紧接着的实测回填又会改写
    // scrollTop，而改 scrollTop 会再派发 scroll 事件 → 又重建 → 高度又变。这条跨帧闭环
    // （PAPER_MEASURE_PASSES 只管得住同一次调用内的递归）就是「滚动时画面疯狂上下跳」的根因。
    // 现在：窗口没变 → 一帧 DOM 操作都不做；窗口变了 → 只增删差异卡片、复用已渲染的卡；
    // 程序改 scrollTop 一律登记 selfScrollTop，不再触发第二轮。

    /** 把卡片 HTML 变成元素，好让窗口变化时能按 q.id 复用（复用即保住 KaTeX 与已加载的题图）。 */
    function createPaperStreamCardEl(q, index) {
        const holder = document.createElement('div');
        holder.innerHTML = renderPaperStreamCard(q, index);
        const card = holder.firstElementChild;
        if (!card) return null;
        card.dataset.vqid = String(q.id);
        card.dataset.vindex = String(index);
        return card;
    }

    function resetPaperVirtualWindow() {
        // container.innerHTML 重设后旧卡片已经脱离文档，缓存必须作废 ——
        // 否则下次同步会把游离的旧元素重新 append 回来。
        paperVirtual.rendered.clear();
        paperVirtual.lastStart = -1;
        paperVirtual.lastEnd = -1;
        paperVirtual.lastTranslate = null;
        paperVirtual.selfScrollTop = null;
    }

    function applyPaperVirtualTranslate(viewport, y) {
        if (paperVirtual.lastTranslate === y) return;
        viewport.style.transform = 'translateY(' + y + 'px)';
        paperVirtual.lastTranslate = y;
    }

    /** 按 q.id 差异同步卡片：只 append 新进视口的、只 remove 滚出视口的。返回新建的卡片。 */
    function syncPaperVirtualWindow(list, start, end) {
        const viewport = document.getElementById('paperVirtualViewport');
        if (!viewport) return [];
        const wanted = new Set();
        for (let i = start; i <= end; i++) wanted.add(list[i].id);

        paperVirtual.rendered.forEach(function (el, id) {
            if (!wanted.has(id)) {
                el.remove();
                paperVirtual.rendered.delete(id);
            }
        });

        const created = [];
        const before = Array.prototype.slice.call(viewport.children);
        for (let i = start; i <= end; i++) {
            const q = list[i];
            let el = paperVirtual.rendered.get(q.id);
            if (el && el.dataset.vindex !== String(i)) {
                // 下标写进了「上移/下移」的 onclick，位置变了就只能重建这一张。
                el.remove();
                paperVirtual.rendered.delete(q.id);
                el = null;
            }
            if (!el) {
                el = createPaperStreamCardEl(q, i);
                if (!el) continue;
                paperVirtual.rendered.set(q.id, el);
                created.push({ el: el, index: i });
            }
            // 已在正确位置上的一张不动，只有新建/移位的才重新摆 —— appendChild 会把
            // 元素移到末尾，按 i 递增追加即得到正确的窗口顺序。
            if (before[i - start] !== el) viewport.appendChild(el);
        }
        return created;
    }

    /** 量窗口内卡片高度并回填缓存，返回是否有变化。 */
    function measurePaperVirtualWindow(list, start) {
        const viewport = document.getElementById('paperVirtualViewport');
        if (!viewport) return false;
        let changed = false;
        const cards = viewport.children;
        for (let k = 0; k < cards.length; k++) {
            const q = list[start + k];
            if (!q) break;
            const measured = cards[k].offsetHeight;
            if (measured > 0 && paperVirtual.heights.get(q.id) !== measured) {
                paperVirtual.heights.set(q.id, measured);
                changed = true;
            }
        }
        return changed;
    }

    /**
     * 实测高度与估计值不一致时前缀和整体平移，正在看的那道题会跳一下，所以补回锚点差值。
     * 但这次 scrollTop 写入必须登记成「自己造成的」：它派发的 scroll 事件不能再触发一轮
     * 重建，否则就是那个让画面持续上下跳的跨帧闭环。
     */
    function anchorPaperVirtualScroll(container, list, prevOffsets) {
        const newOffsets = paperVirtualOffsets(list);
        const spacer = document.getElementById('paperVirtualSpacer');
        if (spacer) spacer.style.height = newOffsets[list.length] + 'px';
        const anchor = paperVirtualIndexAt(prevOffsets, container.scrollTop);
        const delta = newOffsets[anchor] - prevOffsets[anchor];
        if (!delta) return false;
        const target = container.scrollTop + delta;
        if (target === container.scrollTop) return false;
        paperVirtual.selfScrollTop = target;
        container.scrollTop = target;
        return true;
    }

    function updatePaperVirtualList(opts) {
        opts = opts || {};
        paperVirtual.stats.updates++;
        const container = document.getElementById('paperQuestionStream');
        const spacer = document.getElementById('paperVirtualSpacer');
        const viewport = document.getElementById('paperVirtualViewport');
        if (!container || !spacer || !viewport) return;
        const list = paperVirtual.displayList;
        const total = list.length;
        if (total === 0) return;

        // 前缀和定位：start/end 从「卡片顶部的 y」反查，而不是拿固定步长除。
        // 未实测的卡按估计高度参与计算，实测后自动收敛。
        const prevOffsets = paperVirtualOffsets(list);
        spacer.style.height = prevOffsets[total] + 'px';

        const scrollTop = container.scrollTop;
        const viewportH = container.clientHeight;
        let start = paperVirtualIndexAt(prevOffsets, Math.max(0, scrollTop)) - PAPER_OVERSCAN;
        let end = paperVirtualIndexAt(prevOffsets, scrollTop + viewportH) + PAPER_OVERSCAN;
        start = Math.max(0, start);
        end = Math.min(total - 1, end);
        if (start > end) { start = 0; end = Math.min(total - 1, PAPER_OVERSCAN * 2); }

        const windowChanged = !!opts.force
            || start !== paperVirtual.lastStart
            || end !== paperVirtual.lastEnd;

        // 滚动帧里窗口没变 → 一个 DOM 操作都不做，只同步定位。这是本轮修复的核心。
        if (!windowChanged) {
            applyPaperVirtualTranslate(viewport, prevOffsets[start]);
            return;
        }

        paperVirtual.stats.domSyncs++;
        const created = syncPaperVirtualWindow(list, start, end);
        paperVirtual.lastStart = start;
        paperVirtual.lastEnd = end;
        applyPaperVirtualTranslate(viewport, prevOffsets[start]);

        // KaTeX 只对**这一轮新建**的卡片渲染：复用卡上已有的渲染结果原样保留，不重复付出。
        for (let i = 0; i < created.length; i++) {
            const q = list[created[i].index];
            const el = document.getElementById('paper-q-render-' + q.id);
            if (el) {
                window.MathRender.render(el, 'display');
                if (typeof window.adaptChoicesGridLayout === 'function') window.adaptChoicesGridLayout(el);
            }
        }

        // 实测回填：KaTeX 与选项栅格都定型之后再量（它们都会改变卡高）。只在窗口变化时做
        // （低频），量到差异就修正一次 scrollTop；图片懒加载这类「渲染后才长高」的变化由
        // viewport 的 ResizeObserver 兜住（见 remeasurePaperVirtualWindow）。
        if (measurePaperVirtualWindow(list, start)) {
            anchorPaperVirtualScroll(container, list, prevOffsets);
            applyPaperVirtualTranslate(viewport, paperVirtualOffsets(list)[start]);
        }
    }

    /**
     * 图片懒加载、字体就位都会在渲染完成后改变卡高。viewport 绝对定位、高度随内容，
     * 观察它就能兜住这些「渲染后才长高」的情况。
     * 这里只重测、绝不重建 —— 一重建题图就回落到未加载、高度又变回去，观察者会永远震荡。
     */
    function remeasurePaperVirtualWindow() {
        const container = document.getElementById('paperQuestionStream');
        const spacer = document.getElementById('paperVirtualSpacer');
        const viewport = document.getElementById('paperVirtualViewport');
        if (!container || !spacer || !viewport) return;
        const list = paperVirtual.displayList;
        if (!list.length || paperVirtual.lastStart < 0) return;

        const prevOffsets = paperVirtualOffsets(list);
        if (!measurePaperVirtualWindow(list, paperVirtual.lastStart)) return;
        anchorPaperVirtualScroll(container, list, prevOffsets);
        applyPaperVirtualTranslate(viewport, paperVirtualOffsets(list)[paperVirtual.lastStart]);
    }

    function ensurePaperVirtualResizeWatch() {
        const viewport = document.getElementById('paperVirtualViewport');
        if (!viewport || paperVirtual.resizeObs || typeof ResizeObserver === 'undefined') return;
        paperVirtual.resizeObs = new ResizeObserver(function () {
            if (paperVirtual.rafPending) return;
            paperVirtual.rafPending = true;
            requestAnimationFrame(function () {
                paperVirtual.rafPending = false;
                remeasurePaperVirtualWindow();
            });
        });
        paperVirtual.resizeObs.observe(viewport);
    }

    function ensurePaperScrollBinding() {
        const container = document.getElementById('paperQuestionStream');
        if (!container || paperVirtual.bound) return;
        container.addEventListener('scroll', function () {
            // 程序修正 scrollTop 时会派发 scroll 事件。若照单全收，就是
            // 「修正 → scroll → 重建 → 高度又变 → 再修正」的跨帧死循环。
            if (paperVirtual.selfScrollTop !== null) {
                const ours = Math.abs(container.scrollTop - paperVirtual.selfScrollTop) < 1;
                paperVirtual.selfScrollTop = null;
                if (ours) return;
            }
            if (paperVirtual.rafPending) return;
            paperVirtual.rafPending = true;
            requestAnimationFrame(function () {
                paperVirtual.rafPending = false;
                updatePaperVirtualList();
            });
        }, { passive: true });
        paperVirtual.bound = true;
    }

    // Render Part 3: Full-Width Question Stream (虚拟滚动)
    function renderPart3QuestionStream(opts) {
        const container = document.getElementById('paperQuestionStream');
        if (!container) return;

        const list = getPaperDisplayList();
        paperVirtual.displayList = list;
        resetPaperVirtualWindow();
        const total = list.length;
        const resetScroll = !!(opts && opts.resetScroll);
        const prevScroll = resetScroll ? 0 : container.scrollTop;

        if (total === 0) {
            container.innerHTML = `
                <div class="sticky top-0 z-10 bg-slate-50/95 dark:bg-slate-950/95 backdrop-blur pb-2 mb-2 border-b border-slate-200/60 dark:border-slate-700/60">
                    ${paperStreamHeaderHtml()}
                </div>
                <div class="flex flex-col items-center justify-center py-20 bg-white/50 backdrop-blur-md rounded-2xl border border-dashed border-slate-300 dark:bg-slate-800/40 dark:border-slate-700">
                    <div class="w-12 h-12 rounded-2xl bg-brand-50 text-brand-500 flex items-center justify-center text-xl mb-3 dark:bg-slate-800">
                        <i class="fa-solid fa-folder-open"></i>
                    </div>
                    <h4 class="font-semibold text-slate-700 dark:text-slate-200 mb-1">未找到符合条件的题目</h4>
                    <p class="text-xs text-slate-500 max-w-xs text-center">请在上方调节学段、章节、题型、难度或搜索条件。</p>
                </div>
            `;
            if (resetScroll) container.scrollTop = 0;
            resetPaperVirtualWindow();
            return;
        }

        const offsets = paperVirtualOffsets(list);
        container.innerHTML = `
            <div class="sticky top-0 z-10 bg-slate-50/95 dark:bg-slate-950/95 backdrop-blur pb-2 mb-2 border-b border-slate-200/60 dark:border-slate-700/60">
                ${paperStreamHeaderHtml()}
            </div>
            <div id="paperVirtualSpacer" style="position:relative; height:${offsets[total]}px;">
                <div id="paperVirtualViewport" style="position:absolute; top:0; left:0; right:0;"></div>
            </div>
        `;

        if (resetScroll) {
            container.scrollTop = 0;
        } else if (prevScroll > 0) {
            container.scrollTop = prevScroll;
        }
        ensurePaperScrollBinding();
        ensurePaperVirtualResizeWatch();
        updatePaperVirtualList();
    }

    // ---- 题目详情弹窗 (查看答案 -> 弹窗, 不在列表就地展开) ----
    window.openQuestionDetail = function (qid) {
        qid = parseInt(qid, 10);
        if (!qid) return;
        const q = window.PaperStore.questionsMap[qid];
        if (!q) { if (window.showToast) window.showToast('题目数据缺失', 'error'); return; }
        window.closePaperDetail();

        const inCart = window.isInCart(qid);
        const seqNum = (q.seq_num !== undefined) ? q.seq_num : qid;
        const qTypeLabel = getQuestionTypeCn(q.question_type);
        const diffTag = getDifficultyBadge(q.difficulty);

        const backdrop = document.createElement('div');
        backdrop.id = 'paperDetailModalBackdrop';
        backdrop.className = 'fixed inset-0 z-[80] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4';
        backdrop.setAttribute('onclick', 'window.closePaperDetail()');
        backdrop.innerHTML = `
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 shadow-2xl rounded-2xl w-full max-w-3xl max-h-[88vh] flex flex-col overflow-hidden" onclick="event.stopPropagation()">
                <div class="flex items-center justify-between gap-2 px-5 py-3 border-b border-slate-200 dark:border-slate-700">
                    <div class="flex items-center space-x-2 flex-wrap">
                        <span class="font-bold text-slate-800 dark:text-slate-100 text-sm">#${escapeHtml(String(seqNum))}</span>
                        <span class="px-2 py-0.5 rounded-lg text-xs font-semibold bg-brand-50 text-brand-600 border border-brand-200/50 dark:bg-brand-900/30 dark:text-brand-200">${escapeHtml(qTypeLabel)}</span>
                        ${diffTag}
                    </div>
                    <div class="flex items-center gap-2">
                        <button type="button" id="paperDetailCartBtn" onclick="window.toggleCart(${qid})" class="px-3 py-1.5 rounded-xl text-xs font-semibold ${inCart ? 'bg-emerald-500 text-white hover:bg-rose-600' : 'bg-brand-600 text-white shadow-sm hover:bg-brand-700'} active:scale-95 transition-all" data-incart="${inCart ? '1' : '0'}">${inCart ? '移出试卷' : '加入试卷'}</button>
                        <button type="button" onclick="window.closePaperDetail()" class="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-700" title="关闭"><i class="fa-solid fa-xmark"></i></button>
                    </div>
                </div>
                <div class="flex-1 overflow-y-auto p-5 space-y-4">
                    <div class="text-sm leading-relaxed text-slate-800 dark:text-slate-100 overflow-x-auto select-text" id="paperDetailQuestion">${formatQuestionContentHtml(q.content, q.id, getQuestionFigAlign(q), false, false)}</div>
                    <div class="pt-3 border-t border-dashed border-slate-200 dark:border-slate-700">
                        <div class="mb-2 flex items-center gap-2 text-xs font-bold text-brand-700 dark:text-brand-200"><i class="fa-solid fa-signature"></i><span>参考答案与解析</span></div>
                        <div id="paperDetailAnswer" class="text-sm leading-relaxed text-slate-800 dark:text-slate-100 overflow-x-auto select-text"></div>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(backdrop);

        const cartBtn = backdrop.querySelector('#paperDetailCartBtn');
        if (cartBtn) {
            cartBtn.addEventListener('click', () => {
                const nowIn = window.isInCart(qid);
                cartBtn.textContent = nowIn ? '移出试卷' : '加入试卷';
                cartBtn.className = 'px-3 py-1.5 rounded-xl text-xs font-semibold ' + (nowIn ? 'bg-emerald-500 text-white hover:bg-rose-600' : 'bg-brand-600 text-white shadow-sm hover:bg-brand-700') + ' active:scale-95 transition-all';
            });
        }

        const qEl = backdrop.querySelector('#paperDetailQuestion');
        if (qEl) {
            window.MathRender.render(qEl, 'display');
            if (typeof window.adaptChoicesGridLayout === 'function') window.adaptChoicesGridLayout(qEl);
        }
        window.loadPaperDetailAnswer(qid);
    };

    window.closePaperDetail = function () {
        const el = document.getElementById('paperDetailModalBackdrop');
        if (el) el.remove();
    };

    function renderDetailAnswerMath(el) {
        if (el) {
            window.MathRender.render(el, 'display');
        }
    }

    window.loadPaperDetailAnswer = async function (qid) {
        const wrap = document.getElementById('paperDetailAnswer');
        if (!wrap) return;
        qid = parseInt(qid, 10);
        const q = window.PaperStore.questionsMap[qid];
        if (q) seedPaperAnswerCache(q);
        if (hasCachedPaperAnswer(qid)) {
            const ans = window.PaperStore.answerCache[qid] || '';
            wrap.innerHTML = ans.trim()
                ? (typeof window.parseMarkdownWithMath === 'function' ? window.parseMarkdownWithMath(ans) : window.MathBankSafe.sanitizeRichHtml(ans))
                : '<p class="py-3 text-xs text-slate-400 italic">本题暂无答案与解析。</p>';
            renderDetailAnswerMath(wrap);
            return;
        }
        wrap.innerHTML = `<div class="flex items-center gap-2 py-3 text-xs text-slate-500"><i class="fa-solid fa-spinner fa-spin text-brand-500"></i><span>正在加载答案与解析...</span></div>`;
        try {
            const res = await fetch(`/api/questions/${qid}`);
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const detail = await res.json();
            const ans = typeof detail.answer_markdown === 'string' ? detail.answer_markdown : '';
            window.PaperStore.answerCache[qid] = ans;
            if (q) { q.answer_markdown = ans; q.has_answer = Boolean(ans.trim()); }
            wrap.innerHTML = ans.trim()
                ? (typeof window.parseMarkdownWithMath === 'function' ? window.parseMarkdownWithMath(ans) : window.MathBankSafe.sanitizeRichHtml(ans))
                : '<p class="py-3 text-xs text-slate-400 italic">本题暂无答案与解析。</p>';
            renderDetailAnswerMath(wrap);
        } catch (e) {
            wrap.innerHTML = `<div class="flex items-center gap-2 py-3 text-xs text-rose-600"><i class="fa-solid fa-circle-exclamation mr-1"></i>答案加载失败，请重试。</div>`;
        }
    };


    // Render Part 4: Right A4 Canvas & Action Bar
    window.renderPaperCanvas = function () {
        const container = document.getElementById('paperCanvasSection');
        if (!container) return;

        // 保存更新前的 A4 画布与外层 Section 滚动位置，解决重绘导致的视口跳回第一页问题
        const oldSheet = document.getElementById('a4PaperPreviewSheet');
        const savedSheetScrollTop = oldSheet ? oldSheet.scrollTop : 0;
        const savedContainerScrollTop = container ? container.scrollTop : 0;

        const cart = window.PaperStore.cart;
        const meta = window.PaperStore.meta;

        // 卷内 ≥2 科时没有可用的答题卡：现成的 A3 答题卡是按数学 19 题卷切块的
        // （题块对应单选/多选/填空/解答的 19 题布局，卡面上还写死「数学答题卡」），
        // 混科的题块与它对不上，所以直接把入口收起来。
        const isMultiSubjectCart = cartSubjects().length > 1;

        const validCartStats = cart.filter(item => {
            const q = window.PaperStore.questionsMap[item.id];
            return q && q.content && q.content.trim().length > 0;
        });

        const totalScore = validCartStats.reduce((sum, item) => sum + (parseInt(item.score, 10) || 5), 0);
        const totalCount = validCartStats.length;

        // Calculate difficulty ratio (canonical values:
        // 易 = 常规题+易错题, 中 = 挑战题, 难 = 强基题)
        let easyCount = 0, medCount = 0, hardCount = 0;
        validCartStats.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            if (!q) return;
            const d = q.difficulty;
            if (d === 'easy_error' || d === 'normal') easyCount++;
            else if (d === 'challenge') medCount++;
            else if (d === 'qiangji') hardCount++;
            // 其他/未知值不计入 3 档比例条
        });
        const easyPct = totalCount > 0 ? Math.round((easyCount / totalCount) * 100) : 0;
        const medPct = totalCount > 0 ? Math.round((medCount / totalCount) * 100) : 0;
        const hardPct = totalCount > 0 ? Math.max(0, 100 - easyPct - medPct) : 0;

        // 第四格：「记作重做练习」入口 / 已认领状态。
        //
        // 状态挂 PaperStore（面板整块重建），且用卷面指纹兜一道：认领之后又改过题，
        // 那已经是另一份卷面，必须显示成「未认领」让老师重新认一次 —— 否则界面上
        // 写着「已认领」，实际录进闭环的是另一份题序，纸卷与记录对不上。
        const redoAdoptCell = (function () {
            const adopt = window.PaperStore.redoAdopt;
            const sig = paperSignature(cart.map(function (item) { return item.id; }));
            const round = adopt ? Math.max(1, parseInt(adopt.round, 10) || 1) : 1;
            if (adopt && adopt.paperId && adopt.signature === sig) {
                return '<button type="button" id="paperRedoAdoptBtn"'
                    + ' onclick="openRedoPaperFromLibrary(' + adopt.paperId + ')"'
                    + ' class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800 dark:hover:bg-emerald-900/40"'
                    + ' title="已记入错题重做闭环（第 ' + round + ' 轮，卷内 '
                    + Math.max(0, parseInt(adopt.pooledCount, 10) || 0)
                    + ' 道来自错题池）。点它到错题工作台逐题录入对错">'
                    + '<i class="fa-solid fa-rotate-left"></i>'
                    + '<span>已认领 · 第 ' + round + ' 轮</span></button>';
            }
            if (totalCount === 0) {
                return '<button type="button" id="paperRedoAdoptBtn" disabled'
                    + ' class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-400 border border-slate-200 cursor-not-allowed transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-500 dark:border-slate-700"'
                    + ' title="卷面还是空的，先加几道题">'
                    + '<i class="fa-solid fa-rotate-left"></i><span>记作重做练习</span></button>';
            }
            return '<button type="button" id="paperRedoAdoptBtn" onclick="adoptPaperAsRedo()"'
                + ' class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-white text-brand-700 border border-brand-200 hover:bg-brand-50 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-brand-200 dark:border-brand-900 dark:hover:bg-slate-700"'
                + ' title="把这张卷记入错题重做闭环：做完后到错题工作台的「重做复习」逐题录入对错，掌握度和下次重做日才会累积。卷面顺序保持原样，跟你手上那张纸卷一致">'
                + '<i class="fa-solid fa-rotate-left"></i><span>记作重做练习</span></button>';
        })();

        container.innerHTML = `
            <!-- Part 1: Top Fixed Control Section (Non-scrolling Studio Panel) -->
            <div class="shrink-0 mb-3">
                <div class="bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 p-3 rounded-2xl flex flex-col space-y-2.5 shadow-sm">
                    <!-- Row 1: Header Stats & Solution Space Config -->
                    <div class="flex items-center justify-between flex-wrap gap-2 pb-2 border-b border-slate-100 dark:border-slate-800/60">
                        <div class="flex items-center space-x-3">
                            <div class="shrink-0 whitespace-nowrap flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-brand-50 text-brand-700 font-bold text-xs border border-brand-200/60 dark:bg-brand-900/40 dark:text-brand-200 dark:border-brand-900">
                                <span>总分 ${totalScore}</span>
                                <span class="text-slate-400 font-normal">·</span>
                                <span>${totalCount} 题</span>
                            </div>
                            <!-- Difficulty ratio bar -->
                            <div class="hidden xl:flex items-center space-x-1.5 text-xs">
                                <span class="text-slate-400 font-medium">难度比:</span>
                                <div class="w-20 h-2 rounded-full bg-slate-200 overflow-hidden flex dark:bg-slate-700" title="普通题: ${easyPct}% | 挑战题: ${medPct}% | 强基题: ${hardPct}%">
                                    <div class="bg-emerald-500 h-full" style="width: ${easyPct}%"></div>
                                    <div class="bg-amber-500 h-full" style="width: ${medPct}%"></div>
                                    <div class="bg-rose-500 h-full" style="width: ${hardPct}%"></div>
                                </div>
                            </div>
                            <!-- Solution Space Selector -->
                            <div class="shrink-0 flex items-center space-x-1 text-xs">
                                <span class="shrink-0 whitespace-nowrap text-slate-500 font-semibold dark:text-slate-300 flex items-center space-x-1">
                                    <i class="fa-solid fa-arrows-up-down text-brand-500"></i>
                                    <span>留白:</span>
                                </span>
                                <select onchange="window.updateGlobalSolutionSpace(this.value)"
                                    class="whitespace-nowrap px-2 py-1 text-xs rounded-xl border border-brand-200/80 bg-brand-50/60 text-brand-900 font-bold focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-brand-900/50 dark:border-brand-900 dark:text-brand-200">
                                    ${meta.paper_type === 'exam_19' ? `
                                        <option value="0.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '0.0') === 0.0) ? 'selected' : ''}>不留白</option>
                                        <option value="3.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '0.0') === 3.0) ? 'selected' : ''}>紧凑 3cm</option>
                                    ` : `
                                        <option value="0.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '7.0') === 0.0) ? 'selected' : ''}>不留白</option>
                                        <option value="7.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '7.0') === 7.0) ? 'selected' : ''}>标准 7cm</option>
                                    `}
                                </select>
                            </div>
                            <!-- Batch Score by Question Type -->
                            <div class="shrink-0 flex items-center space-x-1 text-xs">
                                <span class="shrink-0 whitespace-nowrap text-slate-500 font-semibold dark:text-slate-300 flex items-center space-x-1" title="按题型批量设置每题分值">
                                    <i class="fa-solid fa-sliders text-brand-500"></i>
                                    <span>设分:</span>
                                </span>
                                <select id="batchScoreTypeSelect" onchange="window.toggleBatchScoreInput()" class="whitespace-nowrap px-2 py-1 text-xs rounded-xl border border-brand-200/80 bg-brand-50/60 text-brand-900 font-bold focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-brand-900/50 dark:border-brand-900 dark:text-brand-200">
                                    <option value="">选题型…</option>
                                    <option value="single_choice">单选</option>
                                    <option value="multi_choice">多选</option>
                                    <option value="fill_in_blank">填空</option>
                                    <option value="detailed_answer">解答</option>
                                </select>
                                <span id="batchScoreInputWrap" class="hidden items-center space-x-1">
                                    <input id="batchScoreValue" type="number" min="0" max="100" value="5" class="w-14 px-2 py-1 text-xs rounded-xl border border-brand-200/80 bg-white text-brand-900 font-bold focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-brand-900/50 dark:border-brand-900 dark:text-brand-200" title="每题分值" />
                                    <button onclick="window.applyBatchScore()" class="px-2 py-1 text-xs rounded-xl bg-brand-600 text-white font-semibold hover:bg-brand-700 active:scale-95 transition-all" title="应用批量分值">
                                        应用
                                    </button>
                                </span>
                            </div>
                        </div>
                    </div>

                    <!-- Row 1.5: Tool entry buttons (Health / Template) -->
                    <div class="flex items-center gap-2.5 pb-2 border-b border-slate-100 dark:border-slate-800/60">
                        <button onclick="window.openHealthModal()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-white text-slate-700 border border-slate-200 hover:bg-slate-100 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="手动触发组卷体检，弹出评估报告">
                            <i class="fa-solid fa-stethoscope text-brand-500"></i>
                            <span>组卷体检</span>
                        </button>
                        <button onclick="window.openTemplateModal()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-white text-slate-700 border border-slate-200 hover:bg-slate-100 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="选择/编辑/新增试卷模板">
                            <i class="fa-solid fa-layer-group text-brand-500"></i>
                            <span>选择试卷模板</span>
                        </button>
                    </div>

                    <!-- Row 2: Paper Management Actions (Clear, Save, History) -->
                    <div class="flex items-center justify-between gap-2.5 pb-2 border-b border-slate-100 dark:border-slate-800/60">
                        ${totalCount > 0 ? `
                            <button onclick="clearCart()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-rose-50/80 text-rose-600 border border-rose-200/70 hover:bg-rose-100/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-rose-950/40 dark:border-rose-800/60 dark:text-rose-300" title="清空当前试卷已选题目">
                                <i class="fa-solid fa-trash-can"></i>
                                <span>清空卷面</span>
                            </button>
                        ` : ''}
                        <button onclick="savePaperToDb()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap" title="保存当前试卷至本地数据库">
                            <i class="fa-solid fa-floppy-disk"></i>
                            <span>保存试卷</span>
                        </button>
                        <button onclick="openSavedPapersModal()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-amber-500 text-white shadow-sm hover:bg-amber-600 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap" title="查阅与管理历史归档试卷">
                            <i class="fa-solid fa-folder-open"></i>
                            <span>历史试卷库</span>
                        </button>
                    </div>

                    <!-- Row 3: 导出格式三格 + 「记作重做练习」一格。
                         第四格是**动作**入口而不是格式：点它就认领这张卷，点 PDF 预览
                         只导出、不问也不认领（认领不再挂在导出成功那一刻 —— 老师印完
                         还要等学生做完，那时才有得录）。四格等分下「LaTeX 打包」只剩
                         ~110px，缩为「LaTeX」，全称留在 title 里。 -->
                    <div class="flex flex-wrap items-center justify-between gap-2 sm:gap-2.5">
                        ${meta.paper_type === 'exam_19' ? `
                            <button onclick="exportPaperPdf('paper')" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="编译并打开试卷 PDF 预览">
                                <i class="fa-solid fa-file-pdf"></i>
                                <span>PDF 预览</span>
                            </button>
                            ${isMultiSubjectCart ? '' : `
                                <button onclick="exportPaperPdf('sheet')" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="编译并打开 A3 双面答题卡 PDF 预览">
                                    <i class="fa-solid fa-file-lines"></i>
                                    <span>答题卡</span>
                                </button>
                            `}
                            <button onclick="exportPaperWord()" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="导出可编辑 Word 试卷正文（不含答题卡）">
                                <i class="fa-solid fa-file-word"></i>
                                <span>Word 导出</span>
                            </button>
                            <button onclick="exportPaperBundle()" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="打包导出 LaTeX 源码、插图及编译好的 PDF 全套文件">
                                <i class="fa-solid fa-box-archive"></i>
                                <span>LaTeX</span>
                            </button>
                        ` : `
                            <button onclick="exportPaperPdf('paper')" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="编译并打开试卷 PDF 预览">
                                <i class="fa-solid fa-file-pdf"></i>
                                <span>PDF 预览</span>
                            </button>
                            <button onclick="exportPaperWord()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="导出保留原生公式的可编辑 Word 试卷">
                                <i class="fa-solid fa-file-word"></i>
                                <span>Word 导出</span>
                            </button>
                            <button onclick="exportPaperBundle()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="打包导出 LaTeX 源码、插图及编译好的 PDF 全套文件">
                                <i class="fa-solid fa-box-archive"></i>
                                <span>LaTeX</span>
                            </button>
                        `}
                        ${redoAdoptCell}
                    </div>

                    <!-- Row 4: 存档状态。草稿任何时候都在本机，只有「保存试卷」才写归档 -->
                    <div class="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 pt-1.5 mt-0.5 border-t border-dashed border-slate-100 dark:border-slate-800/60">
                        <span id="paperSaveStatus" class="min-w-0 text-[10px] font-semibold"></span>
                        <span class="shrink-0 whitespace-nowrap text-[10px] text-slate-400 dark:text-slate-500" title="草稿随时自动暂存在本机；点「保存试卷」才会在历史试卷库里新增一条归档">草稿自动存本机</span>
                    </div>
                </div>
            </div>

            <!-- Part 2: Independent Scrollable A4 Desk Canvas Paper Container -->
            <div class="flex-1 overflow-y-auto custom-scrollbar pt-1 pb-10 flex flex-col items-center" id="a4PaperPreviewSheet">
                ${generateA4PaperPagesHtml(cart, meta, totalCount, totalScore)}
            </div>
        `;

        // Render math in A4 sheet
        const sheet = document.getElementById('a4PaperPreviewSheet');
        if (sheet) {
            try {
                window.MathRender.render(sheet, 'display');
                if (typeof window.adaptChoicesGridLayout === 'function') {
                    window.adaptChoicesGridLayout(sheet);
                }
            } catch (e) { }
            // KaTeX/选项栅格定型后按实测块高重分页：估算分页在这里必然失真
            // （公式、插图、留白的真实高度估不准），失真表现就是题目卡缝/页脚撞字。
            repaginateA4Preview(3);
        }

        // 恢复更新前的滚动位置，保证调排版/留白/格式时在原视口位置零跳跃渲染
        const restoreScroll = () => {
            const newSheet = document.getElementById('a4PaperPreviewSheet');
            if (newSheet && savedSheetScrollTop > 0) {
                newSheet.scrollTop = savedSheetScrollTop;
            }
            const curContainer = document.getElementById('paperCanvasSection');
            if (curContainer && savedContainerScrollTop > 0) {
                curContainer.scrollTop = savedContainerScrollTop;
            }
        };

        restoreScroll();
        if (typeof requestAnimationFrame === 'function') {
            requestAnimationFrame(restoreScroll);
        }

        // 画布整体重绘会换掉提示节点，状态必须跟着重画一次
        renderPaperSaveStatus();
    };

    // 选了 19 题高考卷模板、但卷内混了学科时的提示条：说明为什么排版与平时不同，
    // 免得用户以为是排版坏了。
    function headerMultiSubjectHint(meta) {
        if (!meta || meta.paper_type !== 'exam_19') return '';
        if (cartSubjects().length < 2) return '';
        return `
            <div class="mb-3 text-[11px] text-center font-sans text-amber-800 bg-amber-50/80 border border-amber-200/80 rounded-lg py-1 px-2">
                卷内含多个学科，已按普通考试卷排版 —— 19 题高考卷模板的题号是数学专用，混科会跳号；<br>
                A3 答题卡也只按数学 19 题卷切块，混科套不上，答题卡入口已隐藏。
            </div>
        `;
    }

    function renderA4Header(meta, totalCount, totalScore, totalPages) {
        const isExamType = (meta.paper_type === 'exam' || meta.paper_type === 'exam_19');
        return `
            <!-- Top Secret Mark Bar -->
            ${isExamType ? `
                ${meta.show_secret !== false ? `
                    <div class="group relative flex justify-between items-center mb-3 text-xs font-serif font-bold text-slate-800 pb-1 border border-transparent hover:border-amber-300 hover:bg-amber-50/40 px-2 py-0.5 rounded-lg transition-all duration-200 cursor-default">
                        <span>绝密★启用前</span>
                        <button type="button" onclick="updatePaperMeta('show_secret', false)" 
                                title="点击移除绝密标记"
                                class="opacity-0 group-hover:opacity-100 absolute top-0.5 right-1 bg-amber-500 hover:bg-amber-600 text-white text-[10px] font-sans px-2 py-0.5 rounded-full shadow-md transition-all duration-200 flex items-center space-x-1 cursor-pointer z-20">
                            <i class="fa-solid fa-eye-slash text-[9px]"></i>
                            <span>移除标记</span>
                        </button>
                    </div>
                ` : `
                    <div onclick="updatePaperMeta('show_secret', true)" 
                         title="点击恢复绝密标记"
                         class="mb-3 border border-dashed border-slate-300 hover:border-brand-500 bg-slate-50/50 hover:bg-brand-50/50 rounded-lg py-0.5 px-2 text-xs text-slate-400 hover:text-brand-600 cursor-pointer transition-all duration-200 group select-none flex items-center space-x-1.5 w-fit">
                        <i class="fa-solid fa-circle-plus text-slate-400 group-hover:text-brand-500 text-xs group-hover:scale-110 transition-transform"></i>
                        <span class="font-sans font-medium text-[10px]">已移除绝密标记 (点击在此恢复)</span>
                    </div>
                `}
            ` : ''}

            <!-- Exam Header Title & Subject -->
            <div class="title-header-group text-center mb-3">
                <h1 contenteditable="true"
                    oninput="updatePaperMeta('title', this.innerText)"
                    onblur="saveMetaToStorage()"
                    title="点击直接在试卷上修改主标题"
                    placeholder="+ 点击在此直接添加主标题"
                    class="canvas-meta-title text-2xl font-bold tracking-normal text-slate-900 font-serif mb-1.5 outline-none hover:bg-amber-50/60 focus:bg-white focus:ring-2 focus:ring-brand-200/80 rounded-lg px-3 py-0.5 transition-all cursor-text inline-block min-w-[200px]"
                    spellcheck="false">${(meta.title && meta.title.trim()) ? escapeHtml(meta.title) : ''}</h1>
                <div contenteditable="true"
                     oninput="updatePaperMeta('subject_line', this.innerText)"
                     onblur="saveMetaToStorage()"
                     title="点击填写学科或卷种（如：数学 / 物理 / 理科综合）"
                     placeholder="+ 点击填写学科或卷种"
                     class="canvas-meta-subject text-xl font-bold text-slate-900 font-serif my-2 outline-none hover:bg-amber-50/60 focus:bg-white focus:ring-2 focus:ring-brand-200/80 rounded-lg px-3 py-0.5 transition-all cursor-text min-w-[120px] inline-block"
                     spellcheck="false">${(meta.subject_line && meta.subject_line.trim()) ? escapeHtml(meta.subject_line) : ''}</div>
                <div contenteditable="true"
                     oninput="updatePaperMeta('subtitle', this.innerText)"
                     onblur="saveMetaToStorage()"
                     title="点击直接在试卷上修改副标题/备注"
                     placeholder="+ 点击在此直接添加副标题 / 备注"
                     class="canvas-meta-subtitle text-sm font-bold font-serif text-slate-900 my-1.5 outline-none hover:bg-amber-50/60 focus:bg-white focus:ring-2 focus:ring-brand-200/80 rounded-lg px-3 py-0.5 transition-all cursor-text min-w-[140px] inline-block"
                     spellcheck="false">${(meta.subtitle && meta.subtitle.trim()) ? escapeHtml(meta.subtitle) : ''}</div>
            </div>

            ${headerMultiSubjectHint(meta)}

            ${isExamType ? `
                <div class="text-[12px] text-center font-serif text-slate-800 mb-4 cursor-pointer hover:bg-amber-50/60 rounded-lg transition-all duration-200"
                     onclick="window.editPaperExamDuration()"
                     title="点击修改考试用时">
                    本试卷共 ${totalPages} 页，${totalCount} 题。全卷满分 ${totalScore} 分。考试用时 ${examDurationMinutes(meta)} 分钟。
                </div>

                <!-- Standard LaTeX Notice Block with Interactive Toggle -->
                ${meta.show_notice !== false ? `
                    <div class="group relative mb-5 text-[11.5px] leading-relaxed font-serif text-slate-800 border border-transparent hover:border-amber-300 hover:bg-amber-50/40 p-2.5 rounded-xl transition-all duration-200 cursor-default">
                        <button type="button" onclick="updatePaperMeta('show_notice', false)" 
                                title="点击移除注意事项"
                                class="opacity-0 group-hover:opacity-100 absolute -top-2.5 right-2 bg-amber-500 hover:bg-amber-600 text-white text-[10px] font-sans px-2.5 py-0.5 rounded-full shadow-md transition-all duration-200 flex items-center space-x-1 cursor-pointer z-20">
                            <i class="fa-solid fa-eye-slash text-[9px]"></i>
                            <span>移除注意事项</span>
                        </button>
                        <div class="font-bold mb-1 text-slate-900 text-[12px]">注意事项：</div>
                        <ol class="list-decimal list-inside space-y-0.5 text-slate-800 pl-4">
                            <li>答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。</li>
                            <li>回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑，如需改动，用橡皮擦干净后，再选涂其他答案标号。回答非选择题时，将答案写在答题卡上。写在本试卷上无效。</li>
                            <li>考试结束后，将本试卷和答题卡一并交回。</li>
                        </ol>
                    </div>
                ` : `
                    <div onclick="updatePaperMeta('show_notice', true)" 
                         title="点击恢复注意事项"
                         class="mb-4 my-2 border border-dashed border-slate-300 hover:border-brand-500 bg-slate-50/50 hover:bg-brand-50/50 rounded-xl p-2 text-center text-xs text-slate-400 hover:text-brand-600 cursor-pointer transition-all duration-200 group select-none flex items-center justify-center space-x-1.5">
                        <i class="fa-solid fa-circle-plus text-slate-400 group-hover:text-brand-500 text-sm group-hover:scale-110 transition-transform"></i>
                        <span class="font-sans font-medium text-[10px]">已移除注意事项 (点击在此恢复)</span>
                    </div>
                `}
            ` : ''}
        `;
    }

    function generateA4PaperPagesHtml(cart, meta, totalCount, totalScore) {
        if (cart.length === 0) {
            return `
                <div class="a4-paper-sheet w-full max-w-[794px] min-h-[1123px] bg-white text-slate-900 px-10 py-12 shadow-2xl rounded-sm border border-slate-300 font-serif leading-relaxed relative overflow-hidden select-none">
                    ${renderA4Header(meta, totalCount, totalScore, 1)}
                    <div class="text-center py-24 text-slate-400 font-sans text-xs">暂无试题数据，请在左侧点击“加入试卷”添加题目</div>
                    <div class="absolute bottom-5 left-0 right-0 text-center text-xs font-serif text-slate-700 tracking-wider">${footerSubjectPrefix(meta)}第 1 页 (共 1 页)</div>
                </div>
            `;
        }

        const validCart = cart.filter(item => {
            const q = window.PaperStore.questionsMap[item.id];
            return q && q.content && q.content.trim().length > 0;
        });

        const cartItemsWithIndex = validCart.map((item, idx) => ({ ...item, cartIndex: idx }));

        const typeOrder = ['single_choice', 'multi_choice', 'fill_in_blank', 'detailed_answer'];

        // 两层分组：学科 → 题型。单科卷时外层只有一个学科、且不插部分标题，遍历顺序与
        // 生成的卷面结构同改动前完全一致（这是「单科卷零回归」的根据）。
        const bySubject = {};
        cartItemsWithIndex.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            if (!q) return;
            const qType = q.question_type || 'single_choice';
            const subj = String(q.subject || 'math').toLowerCase();
            if (!bySubject[subj]) bySubject[subj] = {};
            if (!bySubject[subj][qType]) bySubject[subj][qType] = [];
            bySubject[subj][qType].push(item);
        });

        // 学科顺序固定 数学→物理→化学（与题库 Tab、后端 SUBJECT_ORDER 同源）。
        // 万一库里冒出未知学科值，追加到末尾而不是丢掉 —— 整段题在卷面上消失比顺序难看严重得多。
        const subjectKeys = SUBJECT_ORDER.filter(s => bySubject[s])
            .concat(Object.keys(bySubject).filter(s => SUBJECT_ORDER.indexOf(s) < 0));
        const isMultiSubject = subjectKeys.length > 1;

        // 把 (学科 × 题型) 摊平成一张执行清单：单科时等价于原来那一层 typeOrder 遍历，
        // 多学科时每个学科前多插一条「第 N 部分」标题。
        const plan = [];
        subjectKeys.forEach(subjectKey => {
            if (isMultiSubject) plan.push({ kind: 'divider', subjectKey: subjectKey });
            typeOrder.forEach(qType => {
                const items = bySubject[subjectKey][qType];
                if (items && items.length > 0) {
                    plan.push({ kind: 'section', subjectKey: subjectKey, qType: qType, items: items });
                }
            });
        });

        const blocks = [];
        const secNums = ['一', '二', '三', '四', '五'];
        const partNums = ['一', '二', '三', '四', '五'];
        let secIdx = 0;
        let partIdx = 0;
        // 混科卷按普通考试卷排版：exam_19 的题号锚点（单选 1 / 多选 9 / 填空 12 / 解答 15）
        // 是数学新高考卷的结构，混入物化后题号会跳着走。
        const isExam19 = (meta.paper_type === 'exam_19') && !isMultiSubject;
        let globalQIndex = 1;

        plan.forEach(step => {
            if (step.kind === 'divider') {
                // 学科分部分标题。题号（globalQIndex）与大题序号（secIdx）都不在这里重置，
                // 全卷连续 —— 学生看题号不会因为换学科而产生歧义。
                const partItems = [];
                typeOrder.forEach(t => (bySubject[step.subjectKey][t] || []).forEach(it => partItems.push(it)));
                const partScore = partItems.reduce((s, it) => s + (parseInt(it.score, 10) || 5), 0);
                const partNum = partNums[partIdx] || (partIdx + 1);
                partIdx++;
                blocks.push({
                    type: 'subject_divider',
                    subjectKey: step.subjectKey,
                    html: `
                        <div class="paper-subject-block mb-2 mt-4" data-pb-idx="${blocks.length}" data-subject="${step.subjectKey}">
                            <h2 class="text-center font-bold text-[14.5px] font-serif text-slate-900 tracking-wide border-b border-slate-300 pb-1.5 mb-1">第${partNum}部分　${escapeHtml(subjectLabel(step.subjectKey) || step.subjectKey)}（共 ${partItems.length} 题，共 ${partScore} 分）</h2>
                        </div>
                    `,
                    estHeight: 58
                });
                return;
            }

            const qType = step.qType;
            const items = step.items;
            const sectionSubjectKey = isMultiSubject ? step.subjectKey : '';

            // For exam_19: set fixed starting question number according to Gaokao rules
            if (isExam19) {
                if (qType === 'single_choice') globalQIndex = 1;
                else if (qType === 'multi_choice') globalQIndex = 9;
                else if (qType === 'fill_in_blank') globalQIndex = 12;
                else if (qType === 'detailed_answer') globalQIndex = 15;
            }

            const secNum = secNums[secIdx] || (secIdx + 1);
            secIdx++;

            const count = items.length;
            const secScore = items.reduce((s, it) => s + (parseInt(it.score, 10) || 5), 0);
            const unitScore = items[0] ? (parseInt(items[0].score, 10) || 5) : 5;

            let secHeaderText = '';
            if (meta.paper_type === 'quiz') {
                if (qType === 'single_choice') {
                    secHeaderText = `${secNum}、单选题`;
                } else if (qType === 'multi_choice') {
                    secHeaderText = `${secNum}、多选题`;
                } else if (qType === 'fill_in_blank') {
                    secHeaderText = `${secNum}、填空题`;
                } else {
                    secHeaderText = `${secNum}、解答题`;
                }
            } else {
                if (qType === 'single_choice') {
                    secHeaderText = `${secNum}、选择题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，只有一项是符合题目要求的。`;
                } else if (qType === 'multi_choice') {
                    secHeaderText = `${secNum}、多选题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，有多项符合题目要求。全部选对的得 ${unitScore} 分，部分选对的得部分分，有选错的得 0 分。`;
                } else if (qType === 'fill_in_blank') {
                    secHeaderText = `${secNum}、填空题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。`;
                } else {
                    secHeaderText = `${secNum}、解答题：本题共 ${count} 小题，共 ${secScore} 分。解答应写出文字说明、证明过程或演算步骤。`;
                }
            }

            blocks.push({
                type: 'section_title',
                qType: qType,
                html: `
                    <div class="paper-sec-block mb-3" data-qtype="${qType}">
                        <h3 class="font-bold text-[13.5px] font-serif mt-2 mb-2 text-slate-900 leading-snug">${secHeaderText}</h3>
                    </div>
                `,
                estHeight: 40
            });

            items.forEach((item, subIdx) => {
                const q = window.PaperStore.questionsMap[item.id];
                let rawContent = q ? q.content : '';
                const figAlign = getQuestionFigAlign(q);

                let solSpaceCm = 0;
                let isSolSpaceEmbedded = false;

                if (qType === 'detailed_answer') {
                    const defaultFallback = meta.paper_type === 'exam_19' ? '0.0' : '7.0';
                    const defaultSpace = parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : defaultFallback);
                    solSpaceCm = parseFloat(item.solution_space !== undefined ? item.solution_space : defaultSpace);
                    if (isNaN(solSpaceCm)) solSpaceCm = 0.0;

                    if (solSpaceCm > 0 && (figAlign === 'bottom_right' || figAlign === 'center')) {
                        isSolSpaceEmbedded = true;
                    }
                }

                let contentRes = q ? formatQuestionContentHtml(rawContent, q.id, figAlign, isSolSpaceEmbedded) : '';
                let contentHtml = '';
                let embeddedImgHtml = '';

                if (isSolSpaceEmbedded && typeof contentRes === 'object') {
                    contentHtml = contentRes.stemHtml;
                    embeddedImgHtml = contentRes.imgHtml || '';
                } else {
                    contentHtml = typeof contentRes === 'string' ? contentRes : (contentRes.stemHtml || '');
                }

                let stemLine = '';
                if (qType === 'single_choice' || qType === 'multi_choice') {
                    let stemContent = contentHtml;
                    let choicesGrid = '';
                    if (contentHtml.includes('choices-grid') || contentHtml.includes('katex-choices-grid') || contentHtml.includes('grid-cols-')) {
                        const match = contentHtml.match(/([\s\S]*?)(<(?:div|p)[^>]*class="[^"]*(?:choices-grid|katex-choices-grid|grid-cols-[124])"[\s\S]*)/i);
                        if (match) {
                            stemContent = match[1];
                            choicesGrid = match[2];
                        }
                    }
                    if (typeof window.cleanChoiceStemParentheses === 'function') {
                        stemContent = window.cleanChoiceStemParentheses(stemContent);
                    } else {
                        stemContent = stemContent.replace(/(?:[\s\xa0\u3000]*[\(（]\s*\$?\s*(?:\\quad|\\qquad|\\hspace\{.*?\}|[\s\xa0\u3000_])*?\s*\$?\s*[\)）]\s*)+$/, '').replace(/\\paren\b/g, '').trim();
                    }

                    stemLine = `
                        <div class="flex justify-between items-baseline mb-1">
                            <div class="flex-1">${stemContent}</div>
                            <div class="shrink-0 ml-4 font-serif text-slate-900 font-normal select-none">（ &nbsp; ）</div>
                        </div>
                        ${choicesGrid}
                    `;
                } else {
                    stemLine = contentHtml;
                }

                let solutionBlankHtml = '';
                if (qType === 'detailed_answer') {
                    const spacePx = Math.round(solSpaceCm * 35);
                    const isZero = solSpaceCm <= 0;

                    let embeddedImgContainer = '';
                    if (isSolSpaceEmbedded && embeddedImgHtml) {
                        const posClass = figAlign === 'center' ? 'left-1/2 -translate-x-1/2' : 'right-3';
                        embeddedImgContainer = `
                            <div class="absolute ${posClass} top-2 z-10">
                                ${embeddedImgHtml}
                            </div>
                        `;
                    }

                    const minHeightStyle = (isSolSpaceEmbedded && embeddedImgHtml)
                        ? `min-height: ${Math.max(spacePx, 180)}px; height: ${Math.max(spacePx, 180)}px;`
                        : (isZero ? 'min-height: 20px;' : `height: ${spacePx}px;`);

                    solutionBlankHtml = `
                        <div class="solution-space-zone relative ${isZero ? 'py-1 my-1 border-b border-dashed border-slate-200 hover:border-sky-300' : 'mt-2 mb-1 rounded-lg border border-dashed border-sky-300/80 bg-sky-50/20'} group/blank transition-all" style="${minHeightStyle}">
                            ${embeddedImgContainer}
                            <div class="absolute inset-0 flex items-center justify-center pointer-events-none ${isZero ? 'opacity-0 group-hover/blank:opacity-70' : 'opacity-40 group-hover/blank:opacity-80'} transition-opacity">
                                <span class="text-[10px] font-sans text-sky-700 font-medium tracking-wider select-none">
                                    <i class="fa-solid fa-pen-ruler mr-1"></i> 解答题留白区域 (${solSpaceCm.toFixed(1)} cm)
                                </span>
                            </div>
                            <!-- Inline Controls -->
                            <div class="absolute right-2 ${isZero ? '-top-1' : 'bottom-2'} opacity-0 group-hover/blank:opacity-100 transition-opacity flex items-center space-x-1 bg-white/95 backdrop-blur-sm px-2 py-0.5 rounded-lg border border-slate-200 shadow-sm text-[10px] font-sans select-none z-20">
                                <span class="text-slate-400 mr-1 font-medium">留白微调:</span>
                                <button onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, -1.0)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="减少 1cm 留白">
                                    - 1cm
                                </button>
                                <button onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, -0.5)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="减少 0.5cm 留白">
                                    - 0.5
                                </button>
                                <span class="px-1.5 font-bold text-brand-600">${solSpaceCm.toFixed(1)} cm</span>
                                <button onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, 0.5)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="增加 0.5cm 留白">
                                    + 0.5
                                </button>
                                <button onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, 1.0)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="增加 1cm 留白">
                                    + 1cm
                                </button>
                            </div>
                        </div>
                    `;
                }

                const itemHtml = `
                    <div class="paper-q-item group relative text-[13px] leading-normal font-serif p-2 rounded-xl border border-transparent hover:border-brand-200 hover:bg-brand-50/30 transition-all duration-200 cursor-grab active:cursor-grabbing mb-2"
                        draggable="true"
                        data-pb-idx="${blocks.length}"
                        data-qid="${q ? q.id : ''}"
                        data-qtype="${qType}"${isMultiSubject ? ` data-subject="${sectionSubjectKey}"` : ''}
                        data-sub-index="${subIdx}"
                        ondragstart="onPaperCanvasDragStart(event, ${q ? q.id : 0}, ${subIdx}, '${qType}'${isMultiSubject ? `, '${sectionSubjectKey}'` : ''})"
                        ondragover="onPaperCanvasDragOver(event)"
                        ondragenter="onPaperCanvasDragEnter(event)"
                        ondragleave="onPaperCanvasDragLeave(event)"
                        ondragend="onPaperCanvasDragEnd(event)"
                        ondrop="onPaperCanvasDrop(event)">

                        <!-- Hover Action Bar: Drag Handle & Quick Move/Remove Buttons -->
                        <div class="paper-canvas-toolbar absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-[10px] font-sans select-none z-10">
                            <span class="toolbar-label font-medium mr-0.5"><i class="fa-solid fa-grip-vertical"></i> 按住拖拽排序</span>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType('${qType}', ${subIdx}, 'up'${isMultiSubject ? `, '${sectionSubjectKey}'` : ''})" ${subIdx === 0 ? 'disabled' : ''} class="toolbar-btn p-0.5 disabled:opacity-30" title="上移">
                                <i class="fa-solid fa-chevron-up"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType('${qType}', ${subIdx}, 'down'${isMultiSubject ? `, '${sectionSubjectKey}'` : ''})" ${subIdx === items.length - 1 ? 'disabled' : ''} class="toolbar-btn p-0.5 disabled:opacity-30" title="下移">
                                <i class="fa-solid fa-chevron-down"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.removeFromCart(${q ? q.id : 0})" class="toolbar-btn p-0.5 hover:text-rose-500" title="移出试卷">
                                <i class="fa-solid fa-xmark"></i>
                            </button>
                        </div>

                        <div class="flex items-baseline">
                            <span class="font-bold mr-1 text-slate-900 shrink-0">${globalQIndex}.</span>
                            <div class="inline flex-1">
                                ${stemLine}
                                ${solutionBlankHtml}
                            </div>
                        </div>
                    </div>
                `;

                let estH = 75;
                if (qType === 'detailed_answer') estH = 120 + Math.round(solSpaceCm * 35);
                if (rawContent.length > 200) estH += 60;

                blocks.push({
                    type: 'question',
                    qType: qType,
                    html: itemHtml,
                    estHeight: estH
                });

                globalQIndex++;
            });
        });

        // 交给实测重分页循环用的渲染上下文（repaginateA4Preview 读）
        blocks.forEach((b, i) => { b.idx = i; });
        paperA4Ctx = { blocks: blocks, meta: meta, totalCount: totalCount, totalScore: totalScore };

        return renderA4SheetsHtml(paginateA4Blocks(blocks), meta, totalCount, totalScore);
    }

    // A4 预览的渲染上下文：generateA4PaperPagesHtml 写入，repaginateA4Preview 消费。
    let paperA4Ctx = null;

    // 块高口径：优先用渲染后实测的 offsetHeight，没有（未渲染/首帧）退回字符数估算。
    // 块间还有 space-y-1.5 的 6px 间距，预算时要一起算。
    function a4BlockHeight(blk) {
        return (blk.measuredHeight || blk.estHeight) + 6;
    }

    function paginateA4Blocks(blocks) {
        // Group blocks into A4 Page cards
        const pages = [];
        let currentPage = [];
        let currentH = 0;
        const PAGE_1_MAX = 620; // Height budget for Page 1
        const PAGE_N_MAX = 920; // Height budget for Page 2+

        blocks.forEach(blk => {
            const h = a4BlockHeight(blk);
            const maxH = (pages.length === 0) ? PAGE_1_MAX : PAGE_N_MAX;
            if (currentH + h > maxH && currentPage.length > 0) {
                // 混科卷的「第 N 部分」标题不能孤零零落在页末：把页尾连续的标题一起带到下一页。
                // 单科卷没有 divider 块，这段永不触发，分页结果与改动前一致。
                const carried = [];
                while (currentPage.length > 1 && currentPage[currentPage.length - 1].type === 'subject_divider') {
                    carried.unshift(currentPage.pop());
                }
                if (currentPage.length > 0) pages.push(currentPage);
                currentPage = carried.concat([blk]);
                currentH = carried.reduce((s, b) => s + a4BlockHeight(b), 0) + h;
            } else {
                currentPage.push(blk);
                currentH += h;
            }
        });
        if (currentPage.length > 0) {
            pages.push(currentPage);
        }
        return pages;
    }

    function renderA4SheetsHtml(pages, meta, totalCount, totalScore) {
        const totalPages = pages.length;

        // Generate A4 Page Sheet DOM Cards
        let pagesHtml = '';
        pages.forEach((pgBlocks, pgIdx) => {
            const isFirstPage = (pgIdx === 0);
            let pgContent = pgBlocks.map(b => b.html).join('');

            pagesHtml += `
                <div class="a4-paper-sheet w-full max-w-[794px] min-h-[1123px] bg-white text-slate-900 px-10 py-12 shadow-2xl rounded-sm border border-slate-300 font-serif leading-relaxed relative overflow-hidden select-none mb-8">
                    ${isFirstPage ? renderA4Header(meta, totalCount, totalScore, totalPages) : ''}

                    <div class="space-y-1.5 text-[13px]">
                        ${pgContent}
                    </div>

                    <!-- Page Footer -->
                    <div class="absolute bottom-5 left-0 right-0 text-center text-xs font-serif text-slate-700 tracking-wider">
                        ${footerSubjectPrefix(meta)}第 ${pgIdx + 1} 页 (共 ${totalPages} 页)
                    </div>
                </div>
            `;
        });

        return pagesHtml;
    }

    // 实测重分页：分页最初只能按字符数估算（KaTeX 公式、选项栅格、插图、解答留白的
    // 真实高度都估不准），渲染完 KaTeX 后逐块量 offsetHeight 回填，再按实测高度重分页；
    // 分页结果变了就重渲染，直到稳定（有递归上限兜底）。修的是「题目溢出/卡缝、
    // 页脚和题干撞车」这类估算失真问题——与组卷题卡流的实测回填同一思路。
    function repaginateA4Preview(maxPasses) {
        const sheet = document.getElementById('a4PaperPreviewSheet');
        const ctx = paperA4Ctx;
        if (!sheet || !ctx || !ctx.blocks.length) return;

        const pagesSig = pages => pages.map(pg => pg.map(b => b.idx).join(',')).join('|');
        let pages = paginateA4Blocks(ctx.blocks);
        let sig = pagesSig(pages);

        for (let pass = 0; pass < (maxPasses || 3); pass++) {
            sheet.innerHTML = renderA4SheetsHtml(pages, ctx.meta, ctx.totalCount, ctx.totalScore);
            try {
                window.MathRender.render(sheet, 'display');
                if (typeof window.adaptChoicesGridLayout === 'function') window.adaptChoicesGridLayout(sheet);
            } catch (e) { }

            ctx.blocks.forEach(blk => {
                const el = sheet.querySelector('[data-pb-idx="' + blk.idx + '"]');
                const measured = el ? el.offsetHeight : 0;
                if (measured > 0) blk.measuredHeight = measured;
            });

            const nextPages = paginateA4Blocks(ctx.blocks);
            const nextSig = pagesSig(nextPages);
            if (nextSig === sig) return; // 当前 DOM 就是按稳定分页渲染的
            pages = nextPages;
            sig = nextSig;
        }

        // 达到递归上限：至少保证 DOM 与最后一次分页结果一致
        sheet.innerHTML = renderA4SheetsHtml(pages, ctx.meta, ctx.totalCount, ctx.totalScore);
        try {
            window.MathRender.render(sheet, 'display');
            if (typeof window.adaptChoicesGridLayout === 'function') window.adaptChoicesGridLayout(sheet);
        } catch (e) { }
    }
    window.repaginateA4Preview = repaginateA4Preview;

    // Reorder Items strictly within the same Question Type section
    function reorderItemsWithinType(cart, qType, fromSubIdx, toSubIdx, subjectKey) {
        // subjectKey 为空时退化成「只按题型」（单科卷的老口径）；混科卷必须连学科一起匹配，
        // 否则「数学单选」与「物理单选」会互相串位。
        const wantedSubject = String(subjectKey || '').toLowerCase();
        const inSameGroup = (q) => {
            const t = q ? q.question_type : 'single_choice';
            if (t !== qType) return false;
            if (!wantedSubject) return true;
            return String((q && q.subject) || 'math').toLowerCase() === wantedSubject;
        };

        const itemsOfType = [];
        cart.forEach((item) => {
            if (inSameGroup(window.PaperStore.questionsMap[item.id])) {
                itemsOfType.push(item);
            }
        });

        if (fromSubIdx < 0 || fromSubIdx >= itemsOfType.length || toSubIdx < 0 || toSubIdx >= itemsOfType.length) {
            return cart;
        }

        const moved = itemsOfType.splice(fromSubIdx, 1)[0];
        itemsOfType.splice(toSubIdx, 0, moved);

        const newCart = [...cart];
        let subIdx = 0;
        cart.forEach((item, idx) => {
            if (inSameGroup(window.PaperStore.questionsMap[item.id])) {
                newCart[idx] = itemsOfType[subIdx];
                subIdx++;
            }
        });

        return newCart;
    }

    // Move Question Order within same question type
    window.movePaperQuestionWithinType = function (qType, subIndex, direction, subjectKey) {
        const cart = window.PaperStore.cart;
        const targetSubIdx = direction === 'up' ? subIndex - 1 : subIndex + 1;
        window.PaperStore.cart = reorderItemsWithinType(cart, qType, subIndex, targetSubIdx, subjectKey);
        saveCartToStorage();
        renderPart3QuestionStream();
        window.renderPaperCanvas();
    };

    // Real-Time Dynamic Drag and Drop for A4 Paper Canvas Items (Restricted to same question type)
    let draggedItemData = null;
    let dragPlaceholder = null;

    window.onPaperCanvasDragStart = function (e, qid, subIndex, qType, subjectKey) {
        const card = e.currentTarget.closest('.paper-q-item');
        if (!card) return;

        draggedItemData = { 
            qid: parseInt(qid, 10), 
            fromSubIndex: parseInt(subIndex, 10),
            qType: qType,
            subjectKey: String(subjectKey || ''),
            element: card
        };

        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(qid));

        // Create or reuse dynamic drop placeholder
        if (!dragPlaceholder) {
            dragPlaceholder = document.createElement('div');
            dragPlaceholder.className = 'paper-drag-placeholder border-2 border-dashed border-brand-500 bg-brand-50/70 rounded-xl my-2 flex items-center justify-center text-xs font-semibold text-brand-600 shadow-inner transition-all duration-200 select-none';
            dragPlaceholder.style.height = `${Math.max(48, card.offsetHeight - 8)}px`;
            dragPlaceholder.innerHTML = '<span class="flex items-center space-x-1.5"><i class="fa-solid fa-arrow-down-long text-brand-500 animate-bounce"></i> <span>释放在同题型内插入试题</span></span>';
        }

        // Apply drag style to current card after browser creates drag ghost image
        setTimeout(() => {
            if (card) {
                card.classList.add('opacity-30', 'scale-[0.98]', 'bg-slate-100');
                if (card.parentNode) {
                    card.parentNode.insertBefore(dragPlaceholder, card);
                }
            }
        }, 0);
    };

    window.onPaperCanvasDragOver = function (e) {
        e.preventDefault();
        if (!draggedItemData || !dragPlaceholder) return;

        const targetCard = e.target.closest('.paper-q-item');
        if (!targetCard || targetCard === draggedItemData.element) return;

        // Strict boundary: targetCard must belong to the SAME question type AND the same subject.
        // 混科卷里「数学单选」与「物理单选」是两段，跨段插入会让题号与部分归属同时错掉。
        const targetQType = targetCard.dataset.qtype;
        const targetSubject = String(targetCard.dataset.subject || '');
        if (targetQType !== draggedItemData.qType || targetSubject !== draggedItemData.subjectKey) {
            // Different section! Disallow drag placeholder insertion
            e.dataTransfer.dropEffect = 'none';
            return;
        }

        e.dataTransfer.dropEffect = 'move';
        const rect = targetCard.getBoundingClientRect();
        const midY = rect.top + rect.height / 2;

        if (e.clientY < midY) {
            if (targetCard.previousElementSibling !== dragPlaceholder) {
                targetCard.parentNode.insertBefore(dragPlaceholder, targetCard);
            }
        } else {
            if (targetCard.nextElementSibling !== dragPlaceholder) {
                targetCard.parentNode.insertBefore(dragPlaceholder, targetCard.nextElementSibling);
            }
        }
    };

    window.onPaperCanvasDragEnter = function (e) {
        e.preventDefault();
    };

    window.onPaperCanvasDragLeave = function (e) {
        e.preventDefault();
    };

    window.onPaperCanvasDragEnd = function (e) {
        const card = e.currentTarget.closest('.paper-q-item');
        if (card) {
            card.classList.remove('opacity-30', 'scale-[0.98]', 'bg-slate-100');
        }

        // Find new index within the SAME question type section
        if (dragPlaceholder && dragPlaceholder.parentNode && draggedItemData) {
            const container = dragPlaceholder.parentNode;
            const allItems = Array.from(container.children);
            
            let newSubIndex = 0;
            for (let i = 0; i < allItems.length; i++) {
                const child = allItems[i];
                if (child === dragPlaceholder) {
                    break;
                }
                if (child.classList && child.classList.contains('paper-q-item') && child !== draggedItemData.element
                    && child.dataset.qtype === qType && String(child.dataset.subject || '') === subjectKey) {
                    newSubIndex++;
                }
            }

            const fromSubIndex = draggedItemData.fromSubIndex;
            const qType = draggedItemData.qType;
            const subjectKey = draggedItemData.subjectKey;
            
            if (dragPlaceholder.parentNode) {
                dragPlaceholder.parentNode.removeChild(dragPlaceholder);
            }

            if (fromSubIndex !== newSubIndex && fromSubIndex >= 0 && newSubIndex >= 0) {
                window.PaperStore.cart = reorderItemsWithinType(window.PaperStore.cart, qType, fromSubIndex, newSubIndex, subjectKey);

                saveCartToStorage();
                renderPart3QuestionStream();
                window.renderPaperCanvas();
                if (window.showToast) window.showToast(`试题顺序已更新`, 'info');
            } else {
                renderPart3QuestionStream();
                window.renderPaperCanvas();
            }
        } else if (dragPlaceholder && dragPlaceholder.parentNode) {
            dragPlaceholder.parentNode.removeChild(dragPlaceholder);
        }

        draggedItemData = null;
        dragPlaceholder = null;
    };

    window.onPaperCanvasDrop = function (e) {
        e.preventDefault();
        window.onPaperCanvasDragEnd(e);
    };

    // Solution Space Handlers
    window.updateQuestionSolutionSpace = function (qid, delta) {
        qid = parseInt(qid, 10);
        const item = window.PaperStore.cart.find(it => it.id === qid);
        if (!item) return;
        const defaultSpace = parseFloat(window.PaperStore.meta.solution_space_default || '7.0');
        let currentSpace = parseFloat(item.solution_space !== undefined ? item.solution_space : defaultSpace);
        if (isNaN(currentSpace)) currentSpace = 7.0;
        
        let newSpace = Math.max(0.0, Math.min(15.0, Math.round((currentSpace + delta) * 10) / 10));
        item.solution_space = newSpace.toFixed(1);
        saveCartToStorage();
        window.renderPaperCanvas();
        const q = window.PaperStore.questionsMap[qid];
        const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
        if (window.showToast) window.showToast(`题目 #${seqNum} 留白高度设为 ${newSpace.toFixed(1)} cm`, 'info');
    };

    window.updateGlobalSolutionSpace = function (val) {
        const spaceVal = parseFloat(val).toFixed(1);
        window.PaperStore.meta.solution_space_default = spaceVal;
        window.PaperStore.cart.forEach(item => {
            item.solution_space = spaceVal;
        });
        saveMetaToStorage();
        saveCartToStorage();
        window.renderPaperCanvas();
        if (window.showToast) {
            window.showToast(`解答题全局留白设为 ${spaceVal} cm（点击 PDF 预览可即时生效）`, 'success');
        }
    };

    // Toggle visibility of batch score value input based on selected question type
    window.toggleBatchScoreInput = function () {
        const typeSel = document.getElementById('batchScoreTypeSelect');
        const wrap = document.getElementById('batchScoreInputWrap');
        if (!typeSel || !wrap) return;
        if (typeSel.value) {
            wrap.classList.remove('hidden');
            wrap.classList.add('flex');
        } else {
            wrap.classList.add('hidden');
            wrap.classList.remove('flex');
        }
    };

    // Apply a uniform score to all questions of the selected type in the cart
    window.applyBatchScore = function () {
        const typeSel = document.getElementById('batchScoreTypeSelect');
        const valInput = document.getElementById('batchScoreValue');
        if (!typeSel || !valInput) return;
        const qType = typeSel.value;
        if (!qType) {
            if (window.showToast) window.showToast('请先选择题型', 'warning');
            return;
        }
        const newScore = Math.max(0, Math.min(100, parseInt(valInput.value, 10) || 0));
        if (isNaN(newScore)) {
            if (window.showToast) window.showToast('分值无效', 'warning');
            return;
        }
        let affected = 0;
        window.PaperStore.cart.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            const t = q ? (q.question_type || 'single_choice') : 'single_choice';
            if (t === qType) {
                item.score = newScore;
                affected++;
            }
        });
        saveCartToStorage();
        renderPart3QuestionStream();
        window.renderPaperCanvas();
        if (window.showToast) {
            window.showToast(`已将 ${affected} 道${qType === 'single_choice' ? '单选' : qType === 'multi_choice' ? '多选' : qType === 'fill_in_blank' ? '填空' : '解答'}题分值统一设为 ${newScore} 分`, 'success');
        }
    };

    // Helper: build cart questions payload with solution_space
    function buildCartQuestionsPayload() {
        const cart = window.PaperStore.cart;
        const defaultSpace = (window.PaperStore.meta.solution_space_default || '7.0').toString();
        return cart.map((item, idx) => {
            const q = window.PaperStore.questionsMap[item.id] || {};
            return {
                id: item.id,
                score: item.score,
                order: idx + 1,
                figure_align: getQuestionFigAlign(q),
                solution_space: item.solution_space !== undefined ? item.solution_space.toString() : defaultSpace
            };
        });
    }

    // Export PDF, Word, TeX, and Save Handlers
    window.exportPaperPdf = async function (target = 'paper') {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法导出 PDF', 'warning');
            return;
        }

        const targetName = (target === 'sheet') ? '答题卡' : '试卷';
        const iconEmoji = (target === 'sheet') ? '📝' : '📄';

        // Pre-open single tab synchronously during click event -> 100% bypasses popup blockers!
        const tab = window.open('', '_blank');
        setPdfTabLoadingState(tab, `${iconEmoji} ${targetName} PDF 编译中`, iconEmoji, `正在为您在线静默编译 ${targetName} 高清 PDF...`);

        try {
            if (window.showToast) {
                window.showToast(`正在静默编译 ${targetName} PDF...`, 'info');
            }

            const cartQuestions = buildCartQuestionsPayload();

            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                subject_line: window.PaperStore.meta.subject_line || '',
                exam_duration: examDurationMinutes(window.PaperStore.meta),
                paper_type: paperTypeForPayload(),
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                target: target,
                questions: cartQuestions
            };

            const res = await fetch('/api/paper/export/pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok && res.headers.get('content-type')?.includes('application/pdf')) {
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                if (tab && !tab.closed) {
                    tab.location.href = url;
                }
                if (window.showToast) window.showToast(`${targetName} PDF 编译成功！已在新窗口打开`, 'success');
                // 认领不再挂在导出成功这一刻（2026-09-20 用户要求）：面板第四格
                // 「记作重做练习」才是入口 —— 老师印完还要等学生做完，那时才有得录。
            } else {
                let errLog = `${targetName} PDF 编译失败`;
                let errData = {};
                try {
                    errData = await res.json();
                    if (errData.message) errLog = errData.message;
                } catch (e) {}
                if (tab && !tab.closed) {
                    setPdfTabErrorState(tab, `${targetName} PDF 编译失败`, errData.diagnostic, errLog);
                }
                if (window.showToast) window.showToast(errLog, 'error');
            }
        } catch (e) {
            if (tab && !tab.closed) tab.close();
            if (window.showToast) window.showToast('PDF 请求编译异常', 'error');
        }
    };

    function setPdfTabLoadingState(tab, title, iconEmoji, text) {
        if (!tab) return;
        try {
            const safeTitle = escapeHtml(title);
            const safeIcon = escapeHtml(iconEmoji);
            const safeText = escapeHtml(text);
            tab.document.write(`
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <title>${safeTitle}</title>
                    <style>
                        body {
                            margin: 0;
                            padding: 0;
                            background-color: #0f172a;
                            color: #f8fafc;
                            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                            display: flex;
                            min-height: 100vh;
                            align-items: center;
                            justify-content: center;
                        }
                        .card {
                            background: #1e293b;
                            border: 1px solid #334155;
                            border-radius: 16px;
                            padding: 32px 40px;
                            text-align: center;
                            box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5);
                            max-width: 360px;
                        }
                        .icon-box {
                            width: 56px;
                            height: 56px;
                            border-radius: 14px;
                            background: rgba(99, 102, 241, 0.15);
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            font-size: 28px;
                            margin: 0 auto 16px auto;
                        }
                        h2 { margin: 0 0 8px 0; font-size: 18px; font-weight: 700; color: #ffffff; }
                        p { margin: 0 0 20px 0; font-size: 13px; color: #94a3b8; line-height: 1.5; }
                        .status {
                            display: inline-flex;
                            align-items: center;
                            justify-content: center;
                            gap: 8px;
                            color: #818cf8;
                            font-size: 13px;
                            font-weight: 600;
                        }
                        @keyframes spin { 100% { transform: rotate(360deg); } }
                        .spinner {
                            width: 14px;
                            height: 14px;
                            border: 2px solid rgba(99, 102, 241, 0.3);
                            border-top-color: #818cf8;
                            border-radius: 50%;
                            animation: spin 0.8s linear infinite;
                        }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <div class="icon-box">${safeIcon}</div>
                        <h2>${safeTitle}</h2>
                        <p>${safeText}</p>
                        <div class="status">
                            <div class="spinner"></div>
                            <span>LaTeX 引擎静默编译中...</span>
                        </div>
                    </div>
                </body>
                </html>
            `);
            tab.document.close();
        } catch(e) {}
    }

    function setPdfTabErrorState(tab, title, diagnostic, fallbackMessage) {
        if (!tab) return;
        const report = diagnostic || {};
        const fixes = Array.isArray(report.fixes) && report.fixes.length
            ? report.fixes
            : ['返回题目编辑页，核对报错位置附近的公式或排版命令。'];
        const fixesHtml = fixes.map(item => `<li>${escapeHtml(item)}</li>`).join('');

        // Prefer the pinpointed source_context; otherwise fall back to the
        // full compiler log so the user still sees something actionable.
        const sourceContext = report.source_context
            || report.full_log
            || report.technical_error
            || '';
        const sourceLabel = report.source_context ? '查看出错位置附近的 LaTeX 源码' : '查看编译器完整日志';
        const sourceHtml = sourceContext
            ? `<details ${report.source_context ? '' : 'open'}><summary>${sourceLabel}</summary><pre>${escapeHtml(sourceContext)}</pre></details>`
            : '';
        const technicalHtml = (report.technical_error && report.technical_error !== sourceContext)
            ? `<details><summary>查看编译器技术信息</summary><pre>${escapeHtml(report.technical_error)}</pre></details>`
            : '';
        const aiNoteHtml = report.ai_note
            ? `<p class="ai-note">${escapeHtml(report.ai_note)}</p>`
            : '';

        // Pass the raw TeX so the user can copy / download it for debugging.
        const texSource = report.tex_source || '';
        const safeTex = JSON.stringify(texSource);

        try {
            tab.document.open();
            tab.document.write(`
                <!DOCTYPE html>
                <html lang="zh-CN">
                <head>
                    <meta charset="utf-8">
                    <title>${escapeHtml(title)}</title>
                    <style>
                        * { box-sizing: border-box; }
                        body { margin: 0; padding: 36px 20px; background: #f8fafc; color: #1e293b; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }
                        .card { max-width: 760px; margin: 0 auto; background: white; border: 1px solid #e2e8f0; border-radius: 18px; padding: 28px; box-shadow: 0 18px 45px rgba(15,23,42,.08); }
                        .head { display: flex; gap: 14px; align-items: flex-start; }
                        .icon { width: 46px; height: 46px; flex: 0 0 46px; border-radius: 13px; background: #fff1f2; color: #e11d48; display: grid; place-items: center; font-size: 23px; }
                        h1 { margin: 0 0 6px; font-size: 20px; }
                        .badge { display: inline-block; margin-top: 4px; padding: 3px 8px; border-radius: 999px; background: ${report.ai_used ? '#eef2ff' : '#f1f5f9'}; color: ${report.ai_used ? '#4f46e5' : '#64748b'}; font-size: 12px; }
                        .section { margin-top: 22px; padding-top: 18px; border-top: 1px solid #e2e8f0; }
                        h2 { margin: 0 0 8px; font-size: 14px; color: #475569; }
                        p, li { font-size: 14px; line-height: 1.75; }
                        p { margin: 0; }
                        .ai-note { margin-top: 10px; font-size: 12px; color: #64748b; }
                        ol { margin: 6px 0 0; padding-left: 22px; }
                        code { background: #f1f5f9; border-radius: 5px; padding: 2px 5px; }
                        details { margin-top: 14px; border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px 12px; }
                        summary { cursor: pointer; color: #475569; font-size: 13px; font-weight: 600; }
                        pre { margin: 10px 0 0; padding: 12px; border-radius: 8px; overflow: auto; background: #0f172a; color: #e2e8f0; font-size: 12px; line-height: 1.55; white-space: pre-wrap; }
                        .actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 22px; }
                        button { border: 0; border-radius: 9px; padding: 10px 16px; background: #334155; color: white; cursor: pointer; font-size: 13px; }
                        button.ghost { background: #e2e8f0; color: #334155; }
                    </style>
                </head>
                <body>
                    <main class="card">
                        <div class="head">
                            <div class="icon">!</div>
                            <div>
                                <h1>${escapeHtml(report.summary || fallbackMessage || title)}</h1>
                                <p>${escapeHtml(report.location || '试卷公式或模板附近')}</p>
                                <span class="badge">${report.ai_used ? 'AI 已结合编译日志解释' : '本地诊断结果'}</span>
                            </div>
                        </div>
                        <section class="section"><h2>为什么会这样</h2><p>${escapeHtml(report.cause || fallbackMessage || 'LaTeX 编译没有完成。')}</p>${aiNoteHtml}</section>
                        <section class="section"><h2>建议如何修复</h2><ol>${fixesHtml}</ol></section>
                        ${sourceHtml}
                        ${technicalHtml}
                        <div class="actions">
                            <button onclick="window.close()">关闭此页并返回修改</button>
                            ${texSource ? '<button class="ghost" onclick="__copyTex()">复制完整 LaTeX 源码</button><button class="ghost" onclick="__downloadTex()">下载 paper.tex</button>' : ''}
                        </div>
                    </main>
                    <script>
                        const __TEX = ${safeTex};
                        function __copyTex() {
                            if (navigator.clipboard) { navigator.clipboard.writeText(__TEX).then(function(){ alert('LaTeX 源码已复制到剪贴板'); }); }
                            else { alert('当前环境不支持自动复制，请使用下载。'); }
                        }
                        function __downloadTex() {
                            const blob = new Blob([__TEX], { type: 'text/plain;charset=utf-8' });
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url; a.download = 'paper.tex'; a.click();
                            URL.revokeObjectURL(url);
                        }
                    </script>
                </body>
                </html>
            `);
            tab.document.close();
        } catch (e) {}
    }

    window.exportPaperWord = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法导出 Word 试卷', 'warning');
            return;
        }

        try {
            const isExam19 = window.PaperStore.meta.paper_type === 'exam_19';
            if (window.showToast) {
                window.showToast(isExam19 ? '正在生成 Word 试卷及解析压缩包（正文+解析，不含答题卡）...' : '正在生成 Word 试卷及参考答案压缩包...', 'info');
            }
            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                subject_line: window.PaperStore.meta.subject_line || '',
                exam_duration: examDurationMinutes(window.PaperStore.meta),
                paper_type: paperTypeForPayload(),
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                questions: buildCartQuestionsPayload()
            };
            const res = await fetch('/api/paper/export/word', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) {
                let message = 'Word 导出失败';
                try {
                    const data = await res.json();
                    message = data.message || message;
                } catch (e) {}
                if (window.showToast) window.showToast(message, 'error');
                return;
            }

            const blob = await res.blob();
            const rawTitle = (window.PaperStore.meta.title || '试卷').trim();
            const safeTitle = rawTitle.replace(/[/\\?%*:|"<>]/g, '_') || '试卷';
            const filename = `${safeTitle}_Word打包.zip`;
            const nativeCount = parseInt(res.headers.get('X-Word-Native-Formulas') || '0', 10);
            const fallbackCount = parseInt(res.headers.get('X-Word-Fallback-Formulas') || '0', 10);
            const failedCount = parseInt(res.headers.get('X-Word-Failed-Formulas') || '0', 10);

            if (typeof window.showSaveFilePicker === 'function') {
                try {
                    const handle = await window.showSaveFilePicker({
                        suggestedName: filename,
                        types: [{
                            description: 'Zip Archive',
                            accept: { 'application/zip': ['.zip'] }
                        }]
                    });
                    const writable = await handle.createWritable();
                    await writable.write(blob);
                    await writable.close();
                } catch (err) {
                    if (err && err.name === 'AbortError') return;
                    const url = URL.createObjectURL(blob);
                    const anchor = document.createElement('a');
                    anchor.href = url;
                    anchor.download = filename;
                    anchor.click();
                    URL.revokeObjectURL(url);
                }
            } else {
                const url = URL.createObjectURL(blob);
                const anchor = document.createElement('a');
                anchor.href = url;
                anchor.download = filename;
                anchor.click();
                URL.revokeObjectURL(url);
            }

            if (window.showToast) {
                const baseTip = `Word 打包《${filename}》导出成功（含试卷正文与含答案解析两份文档）！`;
                if (failedCount > 0) {
                    window.showToast(`${baseTip}，有 ${failedCount} 处公式已用红字标出`, 'warning');
                } else if (fallbackCount > 0) {
                    window.showToast(`${baseTip}，${nativeCount} 处原生公式，${fallbackCount} 处图片保真公式`, 'warning');
                } else {
                    window.showToast(`${baseTip}，${nativeCount} 处公式均可直接编辑`, 'success');
                }
            }
        } catch (e) {
            if (window.showToast) window.showToast('Word 生成请求异常', 'error');
        }
    };

    window.exportPaperBundle = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法打包导出 LaTeX 资源包', 'warning');
            return;
        }

        try {
            if (window.showToast) window.showToast('正在打包 LaTeX 源码并编译全套 PDF 归档...', 'info');

            const cartQuestions = buildCartQuestionsPayload();

            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                subject_line: window.PaperStore.meta.subject_line || '',
                exam_duration: examDurationMinutes(window.PaperStore.meta),
                paper_type: paperTypeForPayload(),
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                questions: cartQuestions
            };

            const res = await fetch('/api/paper/export/bundle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok) {
                const blob = await res.blob();
                const rawTitle = (window.PaperStore.meta.title || '试卷').trim();
                const safeTitle = rawTitle.replace(/[/\\?%*:|"<>]/g, '_') || '试卷';
                const filename = `${safeTitle}_LaTeX打包.zip`;

                if (typeof window.showSaveFilePicker === 'function') {
                    try {
                        const handle = await window.showSaveFilePicker({
                            suggestedName: filename,
                            types: [{
                                description: 'Zip Archive',
                                accept: { 'application/zip': ['.zip'] }
                            }]
                        });
                        const writable = await handle.createWritable();
                        await writable.write(blob);
                        await writable.close();
                        if (window.showToast) window.showToast(`LaTeX 打包归档已保存至指定目录`, 'success');
                        return;
                    } catch (err) {
                        if (err && err.name === 'AbortError') return;
                    }
                }

                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                a.click();
                URL.revokeObjectURL(url);
                if (window.showToast) window.showToast(`LaTeX 打包《${filename}》导出成功！`, 'success');
            } else {
                const errData = await res.json();
                if (window.showToast) window.showToast(errData.message || '导出失败', 'error');
            }
        } catch (e) {
            if (window.showToast) window.showToast('LaTeX 打包请求异常', 'error');
        }
    };

    window.savePaperToDb = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法保存试卷', 'warning');
            return;
        }

        window.PaperStore.saveInFlight = true;
        window.PaperStore.lastSaveError = '';
        renderPaperSaveStatus();

        try {
            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                subject_line: window.PaperStore.meta.subject_line || '',
                exam_duration: examDurationMinutes(window.PaperStore.meta),
                paper_type: paperTypeForPayload(),
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                questions: cart
            };

            const res = await fetch('/api/paper/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (data.status === 'success') {
                // 保存成功才把当前卷面登记为基准；失败时基准不动，提示条继续报「有改动未保存」
                markPaperSavedAs();
                if (window.showToast) window.showToast('试卷已保存到数据库，题目引用次数已自动更新！', 'success');
            } else {
                window.PaperStore.lastSaveError = data.message || '保存失败';
                if (window.showToast) window.showToast(data.message || '保存失败', 'error');
            }
        } catch (e) {
            window.PaperStore.lastSaveError = '保存试卷请求异常';
            if (window.showToast) window.showToast('保存试卷请求异常', 'error');
        } finally {
            window.PaperStore.saveInFlight = false;
            renderPaperSaveStatus();
        }
    };

    // ----------------- Saved Papers Archive Library Modal -----------------
    window.closeSavedPapersModal = function () {
        const modal = document.getElementById('savedPapersModal');
        if (!modal) return;
        window.MathBankModal.close(modal);
        modal.remove();
    };

    window.openSavedPapersModal = async function () {
        let modal = document.getElementById('savedPapersModal');
        if (modal) {
            window.MathBankModal.close(modal);
            modal.remove();
        }

        modal = document.createElement('div');
        modal.id = 'savedPapersModal';
        modal.className = 'fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-md flex items-center justify-center p-4 animate-in fade-in duration-200';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'savedPapersModalTitle');
        modal.innerHTML = `
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden font-sans">
                <!-- Header -->
                <div class="px-6 py-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-slate-50/50 dark:bg-slate-800/50">
                    <div class="flex items-center space-x-2.5">
                        <div class="w-9 h-9 rounded-2xl bg-amber-500/10 text-amber-600 dark:text-amber-400 flex items-center justify-center font-bold text-lg">
                            <i class="fa-solid fa-folder-open"></i>
                        </div>
                        <div>
                            <h3 id="savedPapersModalTitle" class="font-bold text-slate-800 dark:text-slate-100 text-base">历史试卷归档库</h3>
                            <p class="text-xs text-slate-400">查看、一键载入还原或删除已保存的历史试卷记录</p>
                        </div>
                    </div>
                    <button type="button" data-modal-close onclick="closeSavedPapersModal()" aria-label="关闭历史试卷归档库" class="w-8 h-8 rounded-full hover:bg-slate-200/60 dark:hover:bg-slate-700 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 flex items-center justify-center transition-colors">
                        <i class="fa-solid fa-xmark text-sm"></i>
                    </button>
                </div>

                <!-- Body (Scrollable List) -->
                <div class="p-6 overflow-y-auto flex-1 space-y-3" id="savedPapersListContainer">
                    <div class="text-center py-12 text-slate-400 font-sans text-xs">
                        <i class="fa-solid fa-spinner fa-spin text-xl text-brand-500 mb-2 block"></i>
                        正在获取历史试卷列表...
                    </div>
                </div>

                <!-- Footer -->
                <div class="px-6 py-3.5 border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-800/50 flex items-center justify-between text-xs text-slate-400">
                    <span>共保存 <strong id="savedPaperTotalCount" class="text-slate-700 dark:text-slate-200">0</strong> 份历史试卷</span>
                    <button type="button" onclick="closeSavedPapersModal()" class="px-4 py-1.5 rounded-xl bg-slate-200 text-slate-700 hover:bg-slate-300 font-medium transition-colors dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700">
                        关闭窗口
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        window.MathBankModal.open(modal, { onEscape: window.closeSavedPapersModal });

        try {
            const res = await fetch('/api/papers');
            const data = await res.json();
            const container = document.getElementById('savedPapersListContainer');
            const countEl = document.getElementById('savedPaperTotalCount');

            if (data.status === 'success' && data.data) {
                const papers = data.data;
                if (countEl) countEl.textContent = papers.length;

                if (papers.length === 0) {
                    container.innerHTML = `
                        <div class="text-center py-16">
                            <div class="w-14 h-14 mx-auto rounded-3xl bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600 flex items-center justify-center text-2xl mb-3">
                                <i class="fa-solid fa-box-open"></i>
                            </div>
                            <p class="text-sm font-semibold text-slate-500 dark:text-slate-400">暂无保存的历史试卷</p>
                            <p class="text-xs text-slate-400 mt-1">在组卷工作台中挑选题目后点击“保存试卷”即可归档在此处</p>
                        </div>
                    `;
                    return;
                }

                const paperTypeMap = {
                    'exam_19': { label: '19题高考卷', color: 'bg-indigo-50 text-indigo-600 border-indigo-200 dark:bg-indigo-950/40 dark:border-indigo-800 dark:text-indigo-300' },
                    'exam': { label: '常规试卷', color: 'bg-emerald-50 text-emerald-600 border-emerald-200 dark:bg-emerald-950/40 dark:border-emerald-800 dark:text-emerald-300' },
                    'quiz': { label: '日常小练', color: 'bg-amber-50 text-amber-600 border-amber-200 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-300' },
                    'handout': { label: '讲义/教案', color: 'bg-rose-50 text-rose-600 border-rose-200 dark:bg-rose-950/40 dark:border-rose-800 dark:text-rose-300' }
                };

                container.innerHTML = papers.map(p => {
                    const typeInfo = paperTypeMap[p.paper_type] || { label: '试卷', color: 'bg-slate-100 text-slate-600' };
                    const dateStr = p.created_at ? new Date(p.created_at).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '未知时间';

                    return `
                        <div class="bg-slate-50/70 dark:bg-slate-800/40 border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 flex items-center justify-between hover:border-brand-200 dark:hover:border-brand-600 hover:shadow-md transition-all group">
                            <div class="flex-1 min-w-0 pr-4">
                                <div class="flex items-center space-x-2 mb-1">
                                    <span class="inline-flex items-center text-[10px] font-semibold px-2 py-0.5 rounded-full border ${typeInfo.color}">
                                        ${typeInfo.label}
                                    </span>
                                    ${p.is_redo ? '<span class="inline-flex items-center text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-emerald-50 text-emerald-600 border-emerald-200 dark:bg-emerald-950/40 dark:border-emerald-800 dark:text-emerald-300">' + (p.is_adopted_redo ? '重做卷 · 认领' : '重做卷') + '</span>' : ''}
                                    <h4 class="font-bold text-slate-800 dark:text-slate-100 text-sm truncate group-hover:text-brand-600 transition-colors">${escapeHtml(p.title)}</h4>
                                </div>
                                <div class="flex items-center space-x-4 text-xs text-slate-400">
                                    <span><i class="fa-solid fa-calculator text-[10px] mr-1 text-slate-400"></i>总分: <strong class="text-slate-600 dark:text-slate-300">${p.total_score}分</strong></span>
                                    <span><i class="fa-solid fa-list-check text-[10px] mr-1 text-slate-400"></i>题目数: <strong class="text-slate-600 dark:text-slate-300">${p.question_count}题</strong></span>
                                    <span><i class="fa-regular fa-clock text-[10px] mr-1 text-slate-400"></i>${dateStr}</span>
                                </div>
                                ${p.subtitle ? `<p class="text-xs text-slate-400 mt-1 truncate italic">备注: ${escapeHtml(p.subtitle)}</p>` : ''}
                            </div>
                            <div class="flex items-center space-x-2 shrink-0">
                                ${p.is_redo ? '<button onclick="openRedoPaperFromLibrary(' + p.id + ')" class="px-3 py-1.5 rounded-xl bg-emerald-50 hover:bg-emerald-100 active:scale-95 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 text-xs font-semibold transition-all flex items-center space-x-1" title="到错题工作台逐题录入对错"><i class="fa-solid fa-rotate-left text-[11px]"></i><span>录入结果</span></button>' : ''}
                                <button onclick="loadSavedPaper(${p.id})" class="px-3 py-1.5 rounded-xl bg-brand-500 hover:bg-brand-600 active:scale-95 text-white text-xs font-semibold shadow-sm transition-all flex items-center space-x-1" title="载入试卷至工作台">
                                    <i class="fa-solid fa-arrow-right-to-bracket text-[11px]"></i>
                                    <span>载入试卷</span>
                                </button>
                                <button onclick="quickExportPaperPdf(${p.id})" class="px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 active:scale-95 text-white text-xs font-semibold shadow-sm transition-all flex items-center space-x-1" title="快速编译 PDF">
                                    <i class="fa-solid fa-file-pdf text-[11px]"></i>
                                    <span>PDF</span>
                                </button>
                                <button onclick="deleteSavedPaper(${p.id})" class="p-1.5 rounded-xl text-slate-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors" title="删除此保存试卷">
                                    <i class="fa-solid fa-trash-can text-xs"></i>
                                </button>
                            </div>
                        </div>
                    `;
                }).join('');
            }
        } catch (e) {
            console.error(e);
            const container = document.getElementById('savedPapersListContainer');
            if (container) {
                container.innerHTML = `<div class="text-center py-12 text-rose-500 text-xs">加载历史试卷失败: ${escapeHtml(e.message)}</div>`;
            }
        }
    };

    /** 题目 id 集合的稳定指纹：判断「当前卷面还是不是载入时那一份」。 */
    function paperSignature(ids) {
        return (ids || [])
            .map(function (v) { return parseInt(v, 10) || 0; })
            .sort(function (a, b) { return a - b; })
            .join(',');
    }

    /**
     * 面板第四格「记作重做练习」：把当前卷面认领进错题重做闭环。
     *
     * 有 paperId 且卷面指纹对得上就认领**原来那张卷**（从历史载入后重印的场景）；否则
     * 带上卷面数据让后端现场落库再认领（主编辑器新组的卷，库里本来就没这条记录）。
     *
     * 指纹必须对得上才敢用 loadedPaperId：老师载入卷 A 之后又改了题目，那已经是另一份
     * 卷面 —— 宁可新建一张，也不能把结果记到卷 A 头上。
     *
     * 成功**不跳页**：点它只为了记一笔，而老师此刻多半正要去打印。录对错走第 4 格
     * （认领后会变成绿色入口）或组卷历史卡片上的「录入结果」。
     */
    window.adoptPaperAsRedo = async function () {
        const cart = window.PaperStore.cart || [];
        if (!cart.length) {
            if (window.showToast) window.showToast('卷面还是空的，先加几道题', 'warning');
            return;
        }
        const btn = document.getElementById('paperRedoAdoptBtn');
        if (btn) btn.disabled = true;

        const ids = cart.map(function (item) { return item.id; });
        const signature = paperSignature(ids);
        const knownPaperId = (window.PaperStore.loadedPaperFingerprint === signature)
            ? (parseInt(window.PaperStore.loadedPaperId, 10) || 0)
            : 0;
        const payload = knownPaperId ? { paper_id: knownPaperId } : {
            title: window.PaperStore.meta.title,
            subtitle: window.PaperStore.meta.subtitle,
            subject_line: window.PaperStore.meta.subject_line || '',
            exam_duration: examDurationMinutes(window.PaperStore.meta),
            paper_type: paperTypeForPayload(),
            show_notice: window.PaperStore.meta.show_notice !== false,
            show_secret: window.PaperStore.meta.show_secret !== false,
            questions: cart
        };

        try {
            const res = await fetch('/api/redo/adopt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            let data = {};
            try { data = await res.json(); } catch (e) { data = {}; }
            if (!res.ok || data.status !== 'success') {
                if (window.showToast) window.showToast(data.message || '记作重做练习失败', 'error');
                return;
            }
            window.PaperStore.redoAdopt = {
                paperId: data.paper_id,
                round: data.redo_round,
                pooledCount: data.pooled_count,
                questionCount: data.question_count,
                signature: signature,
                adopted: true
            };
            // 让「当前卷面 = 库里这张卷」这条基准归位：否则再点一次会又新建一张重复卷。
            window.PaperStore.loadedPaperId = data.paper_id;
            window.PaperStore.loadedPaperFingerprint = signature;
            if (window.showToast) {
                window.showToast(
                    data.already
                        ? '这张卷已经在重做练习里了。'
                        : '已记作重做练习，做完到错题工作台逐题录入对错。',
                    'success'
                );
            }
            window.renderPaperCanvas();
            if (typeof window.refreshRedoBadge === 'function') window.refreshRedoBadge();
        } catch (e) {
            if (window.showToast) {
                window.showToast('记作重做练习失败：' + ((e && e.message) || e), 'error');
            }
        } finally {
            const again = document.getElementById('paperRedoAdoptBtn');
            if (again) again.disabled = false;
        }
    };

    window.loadSavedPaper = async function (paperId) {
        try {
            if (window.showToast) window.showToast('正在载入试卷数据...', 'info');

            const res = await fetch(`/api/papers/${paperId}`);
            const data = await res.json();
            if (data.status === 'success' && data.data) {
                const paper = data.data;

                // Update metadata
                window.PaperStore.meta.title = paper.title || '未命名试卷';
                window.PaperStore.meta.subtitle = paper.subtitle || '';
                window.PaperStore.meta.subject_line = paper.subject_line || '';
                window.PaperStore.meta.exam_duration = examDurationMinutes({ exam_duration: paper.exam_duration });
                window.PaperStore.meta.paper_type = paper.paper_type || 'exam';
                // 载入归档即已明确学科行，不要再被预填逻辑覆盖成题库当前学科。
                needsSubjectPrefill = false;
                // 归档里存的就是用户保存时的学科行，视同手改：自动跟随逻辑退避。
                window.PaperStore.meta.subject_line_custom = true;
                window.PaperStore.meta.show_notice = paper.show_notice !== false;
                window.PaperStore.meta.show_secret = paper.show_secret !== false;

                // Rebuild cart & questionsMap
                window.PaperStore.cart = [];
                if (paper.questions && paper.questions.length > 0) {
                    paper.questions.forEach(item => {
                        const qObj = item.question;
                        window.PaperStore.questionsMap[qObj.id] = qObj;
                        window.PaperStore.cart.push({
                            id: qObj.id,
                            score: item.score || 5
                        });
                    });
                }
                // 记住这份卷面是从哪条历史记录载入的 —— 面板上点「记作重做练习」时要
                // 认领原来那张卷，而不是新建一张重复的。指纹在认领时再比对一次才敢用。
                window.PaperStore.loadedPaperId = paper.id;
                window.PaperStore.loadedPaperFingerprint = paperSignature(
                    (paper.questions || []).map(item => item.id)
                );

                // 载入后的卷面已经进内存，顺手落盘 + 把基准对齐到这份归档：否则刷新会退回
                // 载入前的旧草稿，提示条还会拿旧草稿去比新基准，报出假的「有改动未保存」。
                saveMetaToStorage();
                saveCartToStorage();
                markPaperSavedAs(paper.created_at || null);

                // Close modal
                const modal = document.getElementById('savedPapersModal');
                if (modal) modal.remove();

                // Re-render UI
                if (typeof window.renderPaperWorkspace === 'function') {
                    window.renderPaperWorkspace();
                }
                if (window.showToast) window.showToast(`已成功载入试卷: 《${paper.title}》`, 'success');
            } else {
                if (window.showToast) window.showToast(data.message || '载入试卷失败', 'error');
            }
        } catch (e) {
            console.error('Load paper failed:', e);
            if (window.showToast) window.showToast('载入试卷请求失败', 'error');
        }
    };

    window.deleteSavedPaper = async function (paperId) {
        if (!confirm('确定要删除这份历史试卷记录吗？（不会影响题库中的题目数据）')) return;

        try {
            const res = await fetch(`/api/papers/${paperId}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.status === 'success') {
                if (window.showToast) window.showToast('历史试卷已删除', 'success');
                window.openSavedPapersModal();
            } else {
                if (window.showToast) window.showToast(data.message || '删除失败', 'error');
            }
        } catch (e) {
            console.error('Delete paper failed:', e);
            if (window.showToast) window.showToast('删除试卷请求失败', 'error');
        }
    };

    window.quickExportPaperPdf = async function (paperId) {
        try {
            const res = await fetch(`/api/papers/${paperId}`);
            const data = await res.json();
            if (data.status === 'success' && data.data) {
                const paper = data.data;
                const cartQuestions = (paper.questions || []).map(item => ({
                    id: item.id,
                    score: item.score,
                    figure_align: getQuestionFigAlign(item.question)
                }));

                const tab = window.open('', '_blank');
                setPdfTabLoadingState(tab, '📄 试卷 PDF 编译中', '📄', `正在在线静默编译《${paper.title}》高清 PDF...`);

                const pdfRes = await fetch('/api/paper/export/pdf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: paper.title,
                        subtitle: paper.subtitle,
                        paper_type: paper.paper_type,
                        target: 'paper',
                        questions: cartQuestions
                    })
                });

                if (pdfRes.ok && pdfRes.headers.get('content-type')?.includes('application/pdf')) {
                    const blob = await pdfRes.blob();
                    const url = URL.createObjectURL(blob);
                    if (tab && !tab.closed) {
                        tab.location.href = url;
                    }
                } else {
                    let errorData = {};
                    try { errorData = await pdfRes.json(); } catch (e) {}
                    if (tab && !tab.closed) {
                        setPdfTabErrorState(tab, '试卷 PDF 编译失败', errorData.diagnostic, errorData.message || '编译 PDF 失败');
                    }
                    if (window.showToast) window.showToast(errorData.message || '编译 PDF 失败', 'error');
                }
            }
        } catch (e) {
            console.error('Quick export PDF failed:', e);
        }
    };

    // Dynamic Helper utilities
    function escapeHtml(str) {
        return window.MathBankSafe.escapeText(str);
    }

    function getQuestionTypeCn(type) {
        if (!type) return '题目';
        if (window.systemMetadata && Array.isArray(window.systemMetadata.question_types)) {
            const found = window.systemMetadata.question_types.find(t => t.value === type);
            if (found) return found.label;
        }
        const map = {
            single_choice: '单选题',
            multi_choice: '多选题',
            fill_in_blank: '填空题',
            detailed_answer: '解答题'
        };
        return map[type] || type;
    }

    function getDifficultyBadge(diff) {
        if (!diff) return '';
        let label = diff;
        let colorClass = '';
        if (window.systemMetadata && Array.isArray(window.systemMetadata.difficulties)) {
            const found = window.systemMetadata.difficulties.find(d => d.value === diff);
            if (found) {
                label = found.label;
                if (found.color) {
                    colorClass = found.color;
                }
            }
        } else {
            const fallbackMap = {
                easy_error: '易错题',
                normal: '常规题',
                challenge: '挑战题',
                qiangji: '强基题'
            };
            label = fallbackMap[diff] || diff;
        }

        if (!colorClass) {
            if (typeof window.getDifficultyColor === 'function') {
                colorClass = window.getDifficultyColor(diff);
            } else if (diff === 'normal') {
                colorClass = 'text-blue-600 bg-blue-50 border border-blue-200/60 dark:bg-blue-900/30 dark:text-blue-300';
            } else if (diff === 'easy_error') {
                colorClass = 'text-green-600 bg-green-50 border border-green-200/60 dark:bg-green-900/30 dark:text-green-300';
            } else if (diff === 'qiangji') {
                colorClass = 'text-purple-600 bg-purple-50 border border-purple-200/60 dark:bg-purple-900/30 dark:text-purple-300';
            } else if (diff === 'challenge') {
                colorClass = 'text-red-600 bg-red-50 border border-red-200/60 dark:bg-red-900/30 dark:text-red-300';
            } else {
                colorClass = 'text-slate-600 bg-slate-100 border border-slate-200/60';
            }
        }

        const cleanLabel = String(label || '').replace(/[\u2700-\u27BF]|[\uE000-\uF8FF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDFFF]|[\u2011-\u26FF]|\uD83E[\uDD00-\uDFFF]/g, '').trim();
        colorClass = window.MathBankSafe.safeClassList(colorClass, 'text-slate-600 bg-slate-100 border border-slate-200/60');
        return `<span class="px-2 py-0.5 rounded-lg text-xs font-semibold ${colorClass}">${escapeHtml(cleanLabel)}</span>`;
    }

    window.setFigureAlign = function (qid, alignVal) {
        qid = parseInt(qid, 10);
        if (!qid) return;

        // 1. Optimistically update local memory store
        if (window.PaperStore.questionsMap[qid]) {
            window.PaperStore.questionsMap[qid].figure_align = alignVal;
            window.PaperStore.questionsMap[qid].custom_figure_align = alignVal;
        }

        // Close popover
        const existingPopover = document.getElementById('figureAlignPopoverMenu');
        if (existingPopover) existingPopover.remove();

        // 2. Optimistically re-render UI IMMEDIATELY for instant visual feedback!
        if (typeof window.renderPart3QuestionStream === 'function') {
            window.renderPart3QuestionStream();
        }
        if (typeof window.renderPaperCanvas === 'function') {
            window.renderPaperCanvas();
        }

        // 3. Send POST API request to persist in DB (api.js monkey-patch automatically attaches X-Local-Token)
        const formData = new FormData();
        formData.append('figure_align', alignVal);

        fetch(`/api/questions/${qid}/figure_align`, {
            method: 'POST',
            body: formData
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                const labelMap = { 'right': '题干右侧', 'center': '下方居中', 'bottom_right': '下方居右' };
                const q = window.PaperStore.questionsMap[qid];
                const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
                if (window.showToast) window.showToast(`已调整题目 #${seqNum} 插图排版为：${labelMap[alignVal] || alignVal}`, 'success');
            }
        })
        .catch(err => {
            console.error('Update figure_align failed:', err);
        });
    };

    window.showFigureAlignPopover = function (event, qid) {
        event.preventDefault();
        event.stopPropagation();

        qid = parseInt(qid, 10);
        const q = window.PaperStore.questionsMap[qid] || {};
        const currentAlign = getQuestionFigAlign(q);

        // Remove existing popover
        const existingPopover = document.getElementById('figureAlignPopoverMenu');
        if (existingPopover) existingPopover.remove();

        const popover = document.createElement('div');
        popover.id = 'figureAlignPopoverMenu';
        popover.className = 'fixed z-50 bg-white/95 backdrop-blur-md rounded-2xl border border-slate-200 shadow-xl p-2 font-sans text-xs flex flex-col space-y-1 animate-in fade-in zoom-in-95 duration-150 dark:bg-slate-800 dark:border-slate-700 text-slate-800 dark:text-slate-100';

        // Position popover near mouse cursor
        let left = event.clientX + 5;
        let top = event.clientY + 5;

        // Keep inside viewport bounds
        if (left + 170 > window.innerWidth) left = window.innerWidth - 180;
        if (top + 150 > window.innerHeight) top = window.innerHeight - 160;

        popover.style.left = `${left}px`;
        popover.style.top = `${top}px`;

        popover.innerHTML = `
            <div class="px-2 py-1 text-[11px] font-bold text-slate-400 border-b border-slate-100 dark:border-slate-700 flex items-center justify-between">
                <span><i class="fa-solid fa-sliders text-brand-500 mr-1"></i> 调整插图排版位置</span>
                <button onclick="document.getElementById('figureAlignPopoverMenu').remove()" class="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <button onclick="window.setFigureAlign(${qid}, 'right')" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'right' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-right text-xs mr-2 text-brand-500"></i> 题干右侧 (默认)</span>
                ${currentAlign === 'right' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
            <button onclick="window.setFigureAlign(${qid}, 'center')" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'center' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-center text-xs mr-2 text-brand-500"></i> 题干下方居中</span>
                ${currentAlign === 'center' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
            <button onclick="window.setFigureAlign(${qid}, 'bottom_right')" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'bottom_right' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-right text-xs mr-2 text-brand-500"></i> 题干下方居右</span>
                ${currentAlign === 'bottom_right' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
        `;

        document.body.appendChild(popover);

        // Click outside listener
        const closeHandler = function (e) {
            if (!popover.contains(e.target)) {
                popover.remove();
                document.removeEventListener('click', closeHandler);
            }
        };
        setTimeout(() => {
            document.addEventListener('click', closeHandler);
        }, 50);
    };

    function handleFigureAlignImageEvent(event) {
        const image = event.target && event.target.closest
            ? event.target.closest('img[data-figure-align-qid]')
            : null;
        if (!image) return;
        const qid = parseInt(image.dataset.figureAlignQid, 10);
        if (!qid) return;
        event.preventDefault();
        event.stopPropagation();
        window.showFigureAlignPopover(event, qid);
    }

    document.addEventListener('click', handleFigureAlignImageEvent);
    document.addEventListener('contextmenu', handleFigureAlignImageEvent);

    function formatQuestionContentHtml(raw, qid = null, figAlign = 'right', embedInSolSpace = false, showControls = true) {
        if (!raw) return embedInSolSpace ? { stemHtml: '', imgHtml: null } : '';
        let html = String(raw).trim();
        figAlign = figAlign || 'center'; // 默认居中（与 getQuestionFigAlign 同口径）

        if (typeof window.cleanChoiceStemParentheses === 'function' && (html.includes('choices') || html.match(/^\s*[-*]?\s*[A-D][\.、\s]/m))) {
            if (html.includes('\\begin{choices}')) {
                const parts = html.split('\\begin{choices}');
                parts[0] = window.cleanChoiceStemParentheses(parts[0]);
                html = parts[0] + '\\begin{choices}' + parts[1];
            } else {
                html = window.cleanChoiceStemParentheses(html);
            }
        }

        // 1. Extract ALL Markdown image syntaxes ![](/static/uploads/xxx.png) BEFORE KaTeX processing
        //    「什么算一张内联图」由 MathRender 统一判定（2026-09-21）—— 原先这里另有一份
        //    正则与安全判断，抄着抄着就和别处不齐了。这里只负责版式（右侧 / 下方居中等）。
        const stripped = window.MathRender.stripInlineFigures(html);
        const imgSrcList = stripped.urls;
        html = stripped.html.trim();


        // 2. Process LaTeX formulas, \underline, choices environment & LaTeX standard paragraphs via preprocessFormulaForKaTeX
        if (typeof window.parseMarkdownWithMath === 'function') {
            html = window.parseMarkdownWithMath(html);
        } else {
            html = window.MathBankSafe.sanitizeRichHtml(html);
        }

        const stemText = html;

        if (imgSrcList.length > 0) {
            // 如果存在多张插图且原设定为右侧，默认自动优化调整为下方居中 (center) 展示
            const effectiveAlign = (imgSrcList.length > 1 && figAlign === 'right') ? 'center' : (figAlign || 'right');
            const alignLabelMap = {
                'right': '题干右侧',
                'center': '下方居中',
                'bottom_right': '下方居右'
            };
            const currentLabel = alignLabelMap[effectiveAlign] || '下方居中';
            const iconClass = effectiveAlign === 'center' ? 'fa-align-center' : 'fa-align-right';
            const qidAttr = parseInt(qid, 10) || 0;
            const countTag = imgSrcList.length > 1 ? ` (${imgSrcList.length}图)` : '';

            const imgClass = showControls 
                ? `${imgSrcList.length > 1 ? 'max-w-[150px] max-h-[140px]' : 'max-w-[200px] max-h-[170px]'} object-contain rounded-lg border border-slate-200 shadow-sm cursor-pointer hover:ring-2 hover:ring-brand-500 hover:scale-[1.02] transition-all inline-block`
                : `${imgSrcList.length > 1 ? 'max-w-[150px] max-h-[140px]' : 'max-w-[200px] max-h-[170px]'} object-contain rounded-lg border border-slate-200 shadow-sm inline-block`;

            const imgsHtml = imgSrcList.map((src, idx) => {
                const controlAttrs = showControls
                    ? `data-figure-align-qid="${qidAttr}" title="点击或右击可切换插图排版位置 (图${idx + 1} 当前: ${currentLabel})"`
                    : '';
                return `<img src="${window.MathBankSafe.escapeAttribute(src)}" alt="题目配图 ${idx + 1}" class="${imgClass}" ${controlAttrs} loading="lazy" decoding="async">`;
            }).join('');

            const btnHtml = showControls ? `
                <div class="mt-1 ${effectiveAlign === 'center' ? 'text-center' : 'text-right'}">
                    <button onclick="event.stopPropagation(); window.showFigureAlignPopover(event, ${qidAttr})" class="inline-flex items-center text-[10px] font-sans text-brand-700 bg-brand-50 hover:bg-brand-100 border border-brand-200/80 rounded-md px-1.5 py-0.5 transition-colors shadow-sm">
                        <i class="fa-solid ${iconClass} text-[9px] mr-1 text-brand-500"></i> ${currentLabel}${countTag} <i class="fa-solid fa-chevron-down text-[8px] ml-1 opacity-70"></i>
                    </button>
                </div>
            ` : '';

            const imgControlHtml = `
                <div class="inline-block relative group/fig">
                    <div class="flex flex-wrap items-center ${effectiveAlign === 'center' ? 'justify-center' : 'justify-end'} gap-2">
                        ${imgsHtml}
                    </div>
                    ${btnHtml}
                </div>
            `;

            if (embedInSolSpace && (effectiveAlign === 'center' || effectiveAlign === 'bottom_right')) {
                return {
                    stemHtml: `<div>${stemText}</div>`,
                    imgHtml: imgControlHtml,
                    figAlign: effectiveAlign
                };
            }

            if (effectiveAlign === 'center') {
                return `<div>${stemText}</div><div class="my-2 text-center">${imgControlHtml}</div>`;
            } else if (effectiveAlign === 'bottom_right') {
                return `<div>${stemText}</div><div class="my-2 text-right">${imgControlHtml}</div>`;
            } else { // default 'right': Give text 70%+ dominant width, constrain figure container to 160px
                const rightImgsHtml = imgSrcList.map((src, idx) => {
                    const controlAttrs = showControls
                        ? `data-figure-align-qid="${qidAttr}" title="点击或右击可切换插图排版位置 (图${idx + 1} 当前: ${currentLabel})"`
                        : '';
                    const rightImgClass = showControls
                        ? `${imgSrcList.length > 1 ? 'max-w-[125px] max-h-[115px]' : 'max-w-[155px] max-h-[135px]'} object-contain rounded-lg border border-slate-200 shadow-sm cursor-pointer hover:ring-2 hover:ring-brand-500 hover:scale-[1.02] transition-all inline-block`
                        : `${imgSrcList.length > 1 ? 'max-w-[125px] max-h-[115px]' : 'max-w-[155px] max-h-[135px]'} object-contain rounded-lg border border-slate-200 shadow-sm inline-block`;
                    return `<img src="${window.MathBankSafe.escapeAttribute(src)}" alt="题目配图 ${idx + 1}" class="${rightImgClass}" ${controlAttrs} loading="lazy" decoding="async">`;
                }).join('');

                const rightImgControlHtml = `
                    <div class="inline-block relative group/fig">
                        <div class="flex flex-wrap items-center justify-end gap-1.5">
                            ${rightImgsHtml}
                        </div>
                        ${btnHtml}
                    </div>
                `;

                return `
                    <div class="flex items-start justify-between gap-3 my-1">
                        <div class="flex-1 min-w-0 pr-1" style="max-width: calc(100% - 170px);">${stemText}</div>
                        <div class="shrink-0 text-right" style="width: 160px; max-width: 160px;">${rightImgControlHtml}</div>
                    </div>
                `;
            }
        }
        
        if (embedInSolSpace) {
            return { stemHtml: stemText, imgHtml: null, figAlign: figAlign };
        }

        return stemText;
    }

    // Init on DOMContentLoaded
    document.addEventListener('DOMContentLoaded', function () {
        loadStateFromStorage();
        updateCartBadges();
        renderPaperSaveStatus();

        // Restore active workspace if same server instance run (page refresh / tab re-open)
        const currentServerId = window.__serverInstanceId || '';
        let savedServerId = '';
        let savedWorkspace = 'bank';
        try {
            savedServerId = localStorage.getItem('mathbank_server_instance_id') || '';
            savedWorkspace = localStorage.getItem('mathbank_active_workspace') || 'bank';
        } catch (e) { }

        if (currentServerId && savedServerId === currentServerId) {
            if (savedWorkspace === 'paper') {
                if (typeof window.selectWorkspace === 'function') {
                    window.selectWorkspace('paper', '组卷工作台');
                }
            }
        } else {
            // Fresh server startup (.command / .bat re-launch) -> reset to bank studio default
            try {
                localStorage.setItem('mathbank_active_workspace', 'bank');
                if (currentServerId) {
                    localStorage.setItem('mathbank_server_instance_id', currentServerId);
                }
            } catch (e) { }
        }
        document.documentElement.classList.remove('init-ws-paper');
    });

})();
