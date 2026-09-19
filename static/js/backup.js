// ============================================================================
// backup.js —— 设置 → 备份与还原
// ----------------------------------------------------------------------------
// 纯前端模块，不依赖构建工具。加载顺序在 index.html 末尾（onboarding 之后）。
//
// 职责：
//   1. 列出 data_backup/snapshots/ 下的完整快照（时间 / 体积 / 题量）；
//   2. 显示「上次备份时间」与快照保留数；
//   3. 「立即备份」—— 直接调用 POST /api/backup；
//   4. 「校验」单个快照（只读，POST /api/backup/verify）；
//   5. 「延迟还原」——登记一条带时效的请求，真正的换库发生在题库下次启动时。
//
// 为什么不在这里直接把库换掉：题库服务启动时持有 runtime lock（main.py:226），
// 而 restore_full_backup() 内部要拿同一把锁（backup.py:1149）。flock 是「每个打开
// 文件描述」级别的，同进程再开一个 fd 也会撞锁。拆锁就等于允许「服务一边写、库一边
// 被整个换掉」，所以宁可多一次重启：这里只登记请求 + 为当前库做一份完整备份。
// ============================================================================

(function () {
    'use strict';

    const ENDPOINT_LIST = '/api/backups';
    const ENDPOINT_CREATE = '/api/backup';
    const ENDPOINT_VERIFY = '/api/backup/verify';
    const ENDPOINT_RESTORE = '/api/backup/restore';

    // 快照 manifest 里 row_counts 的真实键名 → 界面标签
    const ROW_LABELS = {
        questions: '题目',
        question_curriculums: '章节关联',
        papers: '试卷',
        paper_questions: '试卷题目'
    };

    let snapshots = [];
    let selectedFile = '';
    let pendingRestore = null;
    let restoreTtlSeconds = 0;
    let directoryHint = '';
    let retention = null;
    let verifyResults = Object.create(null); // file -> { ok, message }
    let listBound = false;
    let refreshing = false;

    function $(id) {
        return document.getElementById(id);
    }

    function esc(value) {
        const guard = window.MathBankSafe;
        if (guard && typeof guard.escapeText === 'function') {
            return guard.escapeText(value);
        }
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function escAttr(value) {
        const guard = window.MathBankSafe;
        if (guard && typeof guard.escapeAttribute === 'function') {
            return guard.escapeAttribute(value);
        }
        return esc(value);
    }

    // api.js 的 showToast 是模块内私有函数，从未挂到 window 上（全应用那些
    // `if (window.showToast)` 实际上都是空操作）。这里沿用 mistake.js 的做法，
    // 自建一份、复用同一个 toast DOM，保证提示真的能出现。
    function toast(message, type) {
        const box = $('toast');
        const msg = $('toastMessage');
        const icon = $('toastIconContainer');
        if (!box || !msg) {
            console.log('[Backup]', message);
            return;
        }
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
        clearTimeout(box.__backupTimer);
        box.__backupTimer = setTimeout(function () {
            box.classList.add('translate-y-12', 'opacity-0');
            box.classList.remove('translate-y-0', 'opacity-100');
        }, 3500);
    }

    function setState(text, tone) {
        const node = $('backupPanelState');
        if (!node) return;
        node.textContent = text || '';
        node.className = tone === 'error'
            ? 'text-[10px] text-red-500'
            : (tone === 'busy' ? 'text-[10px] text-brand-500' : 'text-[10px] text-slate-400');
    }

    async function requestJson(url, options) {
        const response = await fetch(url, options);
        let data = {};
        try {
            data = await response.json();
        } catch (error) {
            data = {};
        }
        return { ok: response.ok, status: response.status, data: data || {} };
    }

    function formatClock(value) {
        if (!value) return '—';
        const moment = new Date(value);
        if (isNaN(moment.getTime())) return String(value);
        const pad = function (n) { return String(n).padStart(2, '0'); };
        return moment.getFullYear() + '-' + pad(moment.getMonth() + 1) + '-' + pad(moment.getDate())
            + ' ' + pad(moment.getHours()) + ':' + pad(moment.getMinutes());
    }

    function formatBytes(size) {
        const bytes = Number(size);
        if (!isFinite(bytes) || bytes <= 0) return '—';
        const mb = bytes / 1048576;
        if (mb >= 1024) return (mb / 1024).toFixed(2) + ' GB';
        if (mb >= 1) return mb.toFixed(1) + ' MB';
        return Math.max(1, Math.round(bytes / 1024)) + ' KB';
    }

    function describeRows(rowCounts) {
        if (!rowCounts || typeof rowCounts !== 'object') return '';
        const parts = [];
        Object.keys(ROW_LABELS).forEach(function (key) {
            if (typeof rowCounts[key] === 'number') {
                parts.push(ROW_LABELS[key] + ' ' + rowCounts[key]);
            }
        });
        Object.keys(rowCounts).forEach(function (key) {
            if (ROW_LABELS[key]) return;
            if (typeof rowCounts[key] === 'number') parts.push(key + ' ' + rowCounts[key]);
        });
        return parts.join(' · ');
    }

    function snapshotLabel(snapshot) {
        const manifest = (snapshot && snapshot.manifest) || {};
        return formatClock(manifest.created_at || (snapshot && snapshot.modified_at));
    }

    // ------------------------------------------------------------------
    // 渲染
    // ------------------------------------------------------------------

    function renderSnapshotList() {
        const container = $('backupSnapshotList');
        if (!container) return;

        if (!snapshots.length) {
            container.innerHTML = '<p class="text-[11px] text-slate-400 py-4 text-center">还没有快照。点右上角「立即备份」生成第一份。</p>';
            updateRestoreButton();
            return;
        }

        container.innerHTML = snapshots.map(function (snapshot) {
            const file = snapshot.file;
            const manifest = snapshot.manifest || {};
            const readable = manifest.readable !== false;
            const isSelected = file === selectedFile;
            const isPendingTarget = Boolean(pendingRestore && pendingRestore.archive === file);
            const rows = describeRows(manifest.row_counts);
            const verify = verifyResults[file];

            const meta = [];
            meta.push(formatBytes(snapshot.size_bytes));
            if (rows) meta.push(rows);
            if (typeof manifest.upload_file_count === 'number') {
                meta.push('插图 ' + manifest.upload_file_count);
            }
            if (manifest.app_version) meta.push('v' + manifest.app_version);

            const unreadable = readable
                ? ''
                : '<p class="text-[10px] text-red-500 mt-0.5">manifest 读取失败：'
                    + esc(manifest.error || '未知原因') + '</p>';

            const verifyLine = verify
                ? '<p class="text-[10px] mt-0.5 ' + (verify.ok ? 'text-emerald-600' : 'text-red-500') + '">'
                    + esc(verify.message) + '</p>'
                : '';

            return ''
                + '<div data-backup-file="' + escAttr(file) + '" role="radio" aria-checked="' + (isSelected ? 'true' : 'false') + '" tabindex="0"'
                + ' class="p-2.5 rounded-xl border cursor-pointer transition-all '
                + (isSelected
                    ? 'border-brand-400 bg-brand-50/70 dark:bg-brand-500/10'
                    : 'border-slate-200/70 dark:border-slate-700/60 hover:border-slate-300 dark:hover:border-slate-600')
                + '">'
                +   '<div class="flex items-start gap-2.5">'
                +     '<span class="mt-0.5 h-3.5 w-3.5 shrink-0 rounded-full border-2 '
                +       (isSelected ? 'border-brand-500 bg-brand-500' : 'border-slate-300 dark:border-slate-600')
                +     '"></span>'
                +     '<div class="min-w-0 flex-1">'
                +       '<div class="flex items-center gap-2 flex-wrap">'
                +         '<span class="text-[11px] font-semibold text-slate-700 dark:text-slate-100">'
                +           esc(snapshotLabel(snapshot)) + '</span>'
                +         (isPendingTarget
                            ? '<span class="text-[9px] px-1.5 py-0.5 rounded-full bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-200 font-semibold">待还原目标</span>'
                            : '')
                +       '</div>'
                +       '<p class="text-[10px] text-slate-500 dark:text-slate-400 mt-0.5">' + esc(meta.join(' · ')) + '</p>'
                +       '<p class="text-[9px] text-slate-400 font-mono truncate mt-0.5" title="' + escAttr(file) + '">' + esc(file) + '</p>'
                +       unreadable
                +       verifyLine
                +     '</div>'
                +     '<button type="button" data-backup-verify="' + escAttr(file) + '"'
                +       ' class="shrink-0 text-[9px] px-2 py-1 rounded-lg border border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-300 font-medium hover:bg-slate-50 dark:hover:bg-slate-800 transition-all">'
                +       '<i class="fa-solid fa-shield-halved text-[8px] mr-1"></i>校验</button>'
                +   '</div>'
                + '</div>';
        }).join('');

        updateRestoreButton();
    }

    function renderPendingBox() {
        const box = $('backupPendingBox');
        const text = $('backupPendingText');
        if (!box || !text) return;

        if (!pendingRestore) {
            box.classList.add('hidden');
            box.classList.remove('flex');
            return;
        }

        const safety = pendingRestore.safety_backup
            ? '当前库的安全备份：' + pendingRestore.safety_backup
            : '（未记录安全备份路径）';
        text.textContent = '目标快照 ' + pendingRestore.archive
            + '，登记于 ' + formatClock(pendingRestore.requested_at)
            + '，须在 ' + formatClock(pendingRestore.expires_at) + ' 前重启才生效。'
            + safety;
        box.classList.remove('hidden');
        box.classList.add('flex');
    }

    function renderHeader() {
        const lastAt = $('backupLastAt');
        const badge = $('backupCountBadge');
        const hint = $('backupDirHint');

        let readableCount = 0;
        let latest = '';
        snapshots.forEach(function (snapshot) {
            const manifest = snapshot.manifest || {};
            if (manifest.readable === false) return;
            readableCount += 1;
            if (!latest && manifest.created_at) latest = manifest.created_at;
        });

        if (lastAt) lastAt.textContent = latest ? formatClock(latest) : '尚未备份过';
        if (badge) {
            const retentionText = typeof retention === 'number' ? '（保留最近 ' + retention + ' 份）' : '';
            badge.textContent = readableCount + ' 个可用快照' + retentionText;
        }
        if (hint) {
            hint.textContent = directoryHint || '';
            hint.title = directoryHint || '';
        }
    }

    function updateRestoreButton() {
        const button = $('btnBackupRestore');
        if (!button) return;
        const alreadyPending = Boolean(pendingRestore && pendingRestore.archive === selectedFile);
        button.disabled = !selectedFile || alreadyPending;
        const label = button.querySelector('span');
        if (!label) return;
        if (!selectedFile) {
            label.textContent = '还原到选中的快照';
        } else if (alreadyPending) {
            label.textContent = '该快照已在等待重启还原';
        } else {
            label.textContent = '还原到选中的快照';
        }
    }

    function selectSnapshot(file) {
        if (!file) return;
        selectedFile = file;
        renderSnapshotList();
    }

    // ------------------------------------------------------------------
    // 数据加载
    // ------------------------------------------------------------------

    async function loadBackupPanel() {
        const container = $('backupSnapshotList');
        if (!container || refreshing) return;
        refreshing = true;
        setState('读取中…', 'busy');
        try {
            const result = await requestJson(ENDPOINT_LIST, { method: 'GET' });
            const data = result.data || {};
            if (!result.ok || data.status !== 'success') {
                throw new Error(data.message || ('HTTP ' + result.status));
            }
            snapshots = Array.isArray(data.snapshots) ? data.snapshots : [];
            pendingRestore = data.pending_restore || null;
            restoreTtlSeconds = Number(data.restore_ttl_seconds) || 0;
            directoryHint = data.dir || '';
            retention = typeof data.retention === 'number' ? data.retention : null;

            // 选中的快照若已不在列表里（被 retention 轮转掉），清掉选中态。
            // 注意：这里刻意不做「默认选中最新一份」——还原是破坏性操作，
            // 按钮必须等用户显式点过某一行才可点，不能被默认值预先武装。
            if (selectedFile && !snapshots.some(function (item) { return item.file === selectedFile; })) {
                selectedFile = '';
            }

            renderHeader();
            renderSnapshotList();
            renderPendingBox();
            setState(snapshots.length ? '' : '还没有快照', 'idle');
        } catch (error) {
            snapshots = [];
            renderHeader();
            renderSnapshotList();
            setState('读取失败：' + (error && error.message ? error.message : error), 'error');
        } finally {
            refreshing = false;
        }
    }

    // ------------------------------------------------------------------
    // 动作
    // ------------------------------------------------------------------

    async function runBackupNow() {
        const button = $('btnBackupNow');
        const originalHtml = button ? button.innerHTML : '';
        if (button) {
            button.disabled = true;
            button.classList.add('opacity-60');
            button.innerHTML = '<i class="fa-solid fa-circle-notch animate-spin text-[9px]"></i><span>备份中…</span>';
        }
        setState('正在创建完整备份…', 'busy');
        try {
            const result = await requestJson(ENDPOINT_CREATE, { method: 'POST' });
            const data = result.data || {};
            if (!result.ok || data.status !== 'success') {
                throw new Error(data.message || ('HTTP ' + result.status));
            }
            toast(data.message || '已创建完整备份');
            await loadBackupPanel();
        } catch (error) {
            const message = (error && error.message) ? error.message : error;
            setState('备份失败：' + message, 'error');
            toast('备份失败：' + message, 'error');
        } finally {
            // 无论成功、服务端报错还是网络层 reject，按钮都必须还回去
            if (button) {
                button.disabled = false;
                button.classList.remove('opacity-60');
                button.innerHTML = originalHtml;
            }
        }
    }

    async function verifySnapshot(file) {
        if (!file) return;
        setState('正在校验 ' + file + ' …', 'busy');
        try {
            const result = await requestJson(ENDPOINT_VERIFY, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file: file })
            });
            const data = result.data || {};
            if (result.ok && data.status === 'success') {
                const rows = describeRows(data.row_counts);
                verifyResults[file] = {
                    ok: true,
                    message: '校验通过' + (rows ? '：' + rows : '')
                };
                setState('');
            } else {
                verifyResults[file] = {
                    ok: false,
                    message: data.message || ('校验失败（HTTP ' + result.status + '）')
                };
                setState('校验未通过', 'error');
            }
        } catch (error) {
            verifyResults[file] = {
                ok: false,
                message: '校验请求未送达：' + ((error && error.message) ? error.message : error)
            };
            setState('校验请求未送达', 'error');
        } finally {
            renderSnapshotList();
        }
    }

    function openConfirmModal() {
        const modal = $('backupRestoreConfirmModal');
        if (!modal) return;

        const fileEl = $('backupConfirmFile');
        const ttlEl = $('backupConfirmTtl');
        const summaryEl = $('backupConfirmSummary');

        const snapshot = snapshots.filter(function (item) { return item.file === selectedFile; })[0] || null;
        const manifest = (snapshot && snapshot.manifest) || {};

        if (fileEl) fileEl.textContent = selectedFile;
        if (ttlEl) ttlEl.textContent = String(Math.max(1, Math.round(restoreTtlSeconds / 60)));

        if (summaryEl) {
            const lines = [];
            lines.push('<p>目标快照：<strong>' + esc(snapshotLabel(snapshot)) + '</strong>'
                + (manifest.app_version ? '（v' + esc(manifest.app_version) + '）' : '') + '</p>');
            const rows = describeRows(manifest.row_counts);
            if (rows) lines.push('<p>快照内容：' + esc(rows) + '</p>');
            const verify = verifyResults[selectedFile];
            if (verify) {
                lines.push('<p class="' + (verify.ok ? 'text-emerald-600' : 'text-red-500') + '">校验：'
                    + esc(verify.message) + '</p>');
            }
            if (pendingRestore) {
                lines.push('<p class="text-amber-700 dark:text-amber-300">注意：当前已有一条待还原请求（'
                    + esc(pendingRestore.archive) + '），继续登记会覆盖它。</p>');
            }
            summaryEl.innerHTML = lines.join('');
        }

        modal.classList.remove('hidden');
        if (window.MathBankModal && typeof window.MathBankModal.open === 'function') {
            window.MathBankModal.open(modal, { onEscape: window.closeBackupRestoreConfirm });
        }
        setTimeout(function () {
            modal.classList.remove('opacity-0');
            const surface = modal.querySelector('div');
            if (surface) {
                surface.classList.remove('scale-95');
                surface.classList.add('scale-100');
            }
        }, 50);
    }

    function closeBackupRestoreConfirm() {
        const modal = $('backupRestoreConfirmModal');
        if (!modal) return;
        if (window.MathBankModal && typeof window.MathBankModal.close === 'function') {
            window.MathBankModal.close(modal);
        }
        modal.classList.add('opacity-0');
        const surface = modal.querySelector('div');
        if (surface) {
            surface.classList.remove('scale-100');
            surface.classList.add('scale-95');
        }
        setTimeout(function () {
            modal.classList.add('hidden');
        }, 300);
    }

    function requestBackupRestore() {
        if (!selectedFile) {
            toast('先选一份要还原到的快照', 'info');
            return;
        }
        openConfirmModal();
    }

    async function confirmBackupRestore() {
        if (!selectedFile) {
            toast('先选一份要还原到的快照', 'info');
            return;
        }
        const button = $('btnBackupConfirmRestore');
        const originalText = button ? button.textContent : '';
        if (button) {
            button.disabled = true;
            button.textContent = '登记中…';
        }
        setState('正在校验快照并为当前库做备份…', 'busy');
        try {
            const result = await requestJson(ENDPOINT_RESTORE, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file: selectedFile, confirm: true })
            });
            const data = result.data || {};
            if (!result.ok || data.status !== 'success') {
                throw new Error(data.message || ('HTTP ' + result.status));
            }
            closeBackupRestoreConfirm();
            toast(data.message || '已登记还原请求，请关闭并重新启动题库', 'info');
            await loadBackupPanel();
        } catch (error) {
            // 失败必须留下可读原因，且不能让弹窗假装成功关掉
            const message = (error && error.message) ? error.message : error;
            setState('登记失败：' + message, 'error');
            toast('登记还原请求失败：' + message, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = originalText;
            }
        }
    }

    async function cancelPendingRestore() {
        const button = $('backupPendingBox');
        const trigger = button ? button.querySelector('button') : null;
        const originalText = trigger ? trigger.textContent : '';
        if (trigger) {
            trigger.disabled = true;
            trigger.textContent = '取消中…';
        }
        try {
            const result = await requestJson(ENDPOINT_RESTORE, { method: 'DELETE' });
            const data = result.data || {};
            if (!result.ok || data.status !== 'success') {
                throw new Error(data.message || ('HTTP ' + result.status));
            }
            toast(data.message || '已取消待还原请求');
            await loadBackupPanel();
        } catch (error) {
            const message = (error && error.message) ? error.message : error;
            setState('取消失败：' + message, 'error');
            toast('取消待还原请求失败：' + message, 'error');
        } finally {
            if (trigger) {
                trigger.disabled = false;
                trigger.textContent = originalText;
            }
        }
    }

    // ------------------------------------------------------------------
    // 事件绑定与导出
    // ------------------------------------------------------------------

    function bindListEvents() {
        const container = $('backupSnapshotList');
        if (!container || listBound) return;
        listBound = true;
        container.addEventListener('click', function (event) {
            const verifyButton = event.target.closest('[data-backup-verify]');
            if (verifyButton) {
                event.stopPropagation();
                verifySnapshot(verifyButton.getAttribute('data-backup-verify'));
                return;
            }
            const row = event.target.closest('[data-backup-file]');
            if (row) selectSnapshot(row.getAttribute('data-backup-file'));
        });
        container.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            const row = event.target.closest('[data-backup-file]');
            if (!row) return;
            event.preventDefault();
            selectSnapshot(row.getAttribute('data-backup-file'));
        });
    }

    window.loadBackupPanel = function () {
        bindListEvents();
        return loadBackupPanel();
    };
    window.runBackupNow = runBackupNow;
    window.requestBackupRestore = requestBackupRestore;
    window.confirmBackupRestore = confirmBackupRestore;
    window.closeBackupRestoreConfirm = closeBackupRestoreConfirm;
    window.cancelPendingRestore = cancelPendingRestore;
    window.verifyBackupSnapshot = verifySnapshot;
    window.__backupPanelState = function () {
        // 供自动化夹具读取内部状态，不参与界面逻辑
        return {
            snapshots: snapshots,
            selectedFile: selectedFile,
            pendingRestore: pendingRestore,
            verifyResults: verifyResults,
            restoreTtlSeconds: restoreTtlSeconds,
            directoryHint: directoryHint,
            retention: retention
        };
    };
})();
