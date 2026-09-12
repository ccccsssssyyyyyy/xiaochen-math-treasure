// ============================================================================
// onboarding.js —— 首次启动引导 / 外部依赖引导 / 升级向导
// ----------------------------------------------------------------------------
// 纯前端模块，不依赖构建工具。加载顺序在 index.html 末尾（api/editor/paper 之后）。
// 三块职责：
//   1. 空题库时的三步引导卡片（配 Key → 放题 → 去组卷），可永久关闭；
//   2. 环境与依赖检查弹窗（LaTeX / LibreOffice / Pandoc / API Key），
//      并在 PDF 导出因缺少 xelatex 失败时自动弹出；
//   3. 升级向导：一键完整备份 + 可复制的升级清单。
// ============================================================================

(function () {
    'use strict';

    const DISMISS_KEY = 'mathbank_onboarding_dismissed_v1';
    const UPGRADE_CHECKLIST = [
        '1) 先在「设置 → 关于」或升级弹窗里点一次「立即备份」，确认 data_backup/snapshots/ 下有新的 zip；',
        '2) 用右上角电源按钮安全关闭题库，确认后台已停止（不要直接关终端保留进程）；',
        '3) 把新版 zip 解压到「临时目录」，不要直接解压进原目录；',
        '4) macOS：Finder 里按 Cmd+Shift+. 显示隐藏文件，把新版内容「合并」覆盖到原目录，'
            + '千万不要选「替换整个文件夹」，否则 .env 与 .db 会被删；Windows：全选复制并替换同名文件；',
        '5) 需要保留的文件：math_question_bank.db（+ -wal/-shm）、.env、data_backup/、static/uploads/；',
        '6) 双击新版启动器，它会校验 Release 文件并只清理该删的旧文件。'
    ].join('\n');

    let envCache = null;

    function $(id) {
        return document.getElementById(id);
    }

    function toast(message, type) {
        if (typeof window.showToast === 'function') {
            window.showToast(message, type || 'success');
        }
    }

    async function getJson(url, options) {
        const response = await fetch(url, options);
        if (!response.ok) {
            throw new Error('HTTP ' + response.status);
        }
        return response.json();
    }

    async function copyText(text) {
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
                return true;
            }
        } catch (error) {
            /* 落到下面的兜底方案 */
        }
        try {
            const helper = document.createElement('textarea');
            helper.value = text;
            helper.setAttribute('readonly', 'readonly');
            helper.style.position = 'fixed';
            helper.style.top = '-1000px';
            document.body.appendChild(helper);
            helper.select();
            const ok = document.execCommand('copy');
            document.body.removeChild(helper);
            return ok;
        } catch (error) {
            return false;
        }
    }

    function flashCopied(button) {
        if (!button) return;
        const original = button.innerHTML;
        button.innerHTML = '<i class="fa-solid fa-check text-[9px]"></i><span>已复制</span>';
        setTimeout(function () {
            button.innerHTML = original;
        }, 1600);
    }

    // ------------------------------------------------------------------
    // 1. 环境自检
    // ------------------------------------------------------------------

    window.fetchEnvironmentStatus = async function (force) {
        if (envCache && !force) return envCache;
        try {
            envCache = await getJson('/api/environment');
        } catch (error) {
            envCache = null;
        }
        return envCache;
    };

    function dependencyCards(env) {
        const latex = (env && env.latex) || {};
        const soffice = (env && env.libreoffice) || {};
        const pandoc = (env && env.pandoc) || {};
        const cards = [
            {
                label: 'LaTeX 排版导出',
                ok: !!latex.available,
                detail: latex.available ? ('已就绪 · ' + (latex.engine || 'latex')) : '未安装 · 导出 PDF 不可用'
            },
            {
                label: 'LibreOffice 转换',
                ok: !!soffice.available,
                detail: soffice.available ? '已就绪 · Word 原卷预览可用' : '未安装 · Word 原卷预览不可用'
            },
            {
                label: 'Pandoc 公式转换',
                ok: !!pandoc.available,
                detail: pandoc.available ? '已就绪' : '未安装 · 公式退化为图片'
            },
            {
                label: 'PDF 原生解析引擎',
                ok: !!(env && env.pdf_inspector && env.pdf_inspector.available),
                detail: (env && env.pdf_inspector && env.pdf_inspector.available)
                    ? '已就绪 · 原生矢量直提'
                    : '未安装 · 已平滑降级为视觉 OCR'
            }
        ];
        return cards.map(function (card) {
            const icon = card.ok ? 'fa-circle-check text-emerald-500' : 'fa-circle-exclamation text-amber-500';
            const tone = card.ok
                ? 'border-emerald-200/70 dark:border-emerald-500/30 bg-emerald-50/40 dark:bg-emerald-500/10'
                : 'border-amber-200/70 dark:border-amber-500/30 bg-amber-50/40 dark:bg-amber-500/10';
            return ''
                + '<div class="p-2.5 rounded-xl border ' + tone + '">'
                + '  <div class="flex items-center space-x-1.5">'
                + '    <i class="fa-solid ' + icon + ' text-[11px]"></i>'
                + '    <span class="text-[11px] font-bold text-slate-700 dark:text-slate-200">' + card.label + '</span>'
                + '  </div>'
                + '  <div class="text-[10px] text-slate-500 dark:text-slate-400 mt-1 leading-relaxed">' + card.detail + '</div>'
                + '</div>';
        }).join('');
    }

    window.refreshDepStatus = async function () {
        const grid = $('depStatusGrid');
        if (!grid) return;
        grid.innerHTML = '<div class="p-3 rounded-xl border border-slate-200 dark:border-slate-700 bg-white/70 dark:bg-slate-800/60">'
            + '<div class="text-[11px] text-slate-400">正在检测本机环境...</div></div>';
        const env = await window.fetchEnvironmentStatus(true);
        if (!env) {
            grid.innerHTML = '<div class="p-3 rounded-xl border border-amber-200 dark:border-amber-500/30 bg-amber-50/50 dark:bg-amber-500/10 sm:col-span-2">'
                + '<div class="text-[11px] text-amber-700 dark:text-amber-200">无法读取环境状态（服务可能未就绪）。请确认程序仍在运行后点「重新检测」。</div></div>';
            return;
        }
        grid.innerHTML = dependencyCards(env);
        updateOnboardingEnvBadge(env);
    };

    window.toggleDepSection = function (key) {
        const section = $('depSection-' + key);
        const chevron = $('depChevron-' + key);
        if (!section) return;
        const willShow = section.classList.contains('hidden');
        section.classList.toggle('hidden', !willShow);
        if (chevron) chevron.style.transform = willShow ? 'rotate(180deg)' : '';
    };

    // 设置 → 关于：内嵌的环境自检网格（切换 tab 时刷新）
    window.refreshSettingsEnvGrid = async function () {
        const grid = $('settingsEnvGrid');
        if (!grid) return;
        const env = await window.fetchEnvironmentStatus(true);
        if (!env) {
            grid.innerHTML = '<div class="text-[10px] text-amber-600 dark:text-amber-300 sm:col-span-2">环境检测失败：服务可能未就绪。</div>';
            return;
        }
        grid.innerHTML = dependencyCards(env);
    };


    window.copyDepCommand = async function (button, command) {
        const ok = await copyText(command);
        if (ok) {
            flashCopied(button);
            toast('安装命令已复制，粘贴到终端执行');
        } else {
            toast('复制失败，请手动选中命令复制', 'warning');
        }
    };

    window.openDepGuideModal = async function (focusKey) {
        const modal = $('depGuideModal');
        if (!modal) return;
        modal.classList.remove('hidden');
        if (window.MathBankModal && window.MathBankModal.open) {
            window.MathBankModal.open(modal, { onEscape: window.closeDepGuideModal });
        }
        setTimeout(function () {
            modal.classList.remove('opacity-0');
            const content = modal.querySelector('.glass-modal');
            if (content) content.classList.remove('scale-95');
        }, 10);

        if (focusKey && focusKey !== 'keys') {
            const section = $('depSection-' + focusKey);
            if (section && section.classList.contains('hidden')) {
                window.toggleDepSection(focusKey);
            }
        }
        await window.refreshDepStatus();
    };

    window.closeDepGuideModal = function () {
        const modal = $('depGuideModal');
        if (!modal) return;
        modal.classList.add('opacity-0');
        const content = modal.querySelector('.glass-modal');
        if (content) content.classList.add('scale-95');
        setTimeout(function () {
            modal.classList.add('hidden');
            if (window.MathBankModal && window.MathBankModal.close) {
                window.MathBankModal.close(modal);
            }
        }, 180);
    };

    // PDF 导出失败且原因是缺少 LaTeX 时，主动把引导弹窗推到用户面前。
    function installLatexFailureWatcher() {
        if (typeof window.showToast !== 'function' || window.__latexWatcherInstalled) return;
        const original = window.showToast;
        window.showToast = function (message, type) {
            try {
                const text = String(message || '');
                if (/-?xelatex|TeX Live|MiKTeX|MacTeX/i.test(text)) {
                    setTimeout(function () {
                        window.openDepGuideModal('latex');
                    }, 400);
                }
            } catch (error) {
                /* 观察者绝不干扰原有 toast */
            }
            return original.apply(this, arguments);
        };
        window.__latexWatcherInstalled = true;
    }

    // ------------------------------------------------------------------
    // 2. 升级向导
    // ------------------------------------------------------------------

    window.runUpgradeBackup = async function () {
        const button = $('btnUpgradeBackup');
        const state = $('upgradeBackupState');
        const originalHtml = button ? button.innerHTML : '';
        if (button) {
            button.disabled = true;
            button.classList.add('opacity-60');
            button.innerHTML = '<i class="fa-solid fa-circle-notch animate-spin text-[9px]"></i><span>备份中...</span>';
        }
        try {
            const data = await getJson('/api/backup', { method: 'POST' });
            if (data.status === 'success') {
                if (state) {
                    state.textContent = '已备份 ✓';
                    state.className = 'text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-50 dark:bg-emerald-500/20 text-emerald-600 dark:text-emerald-300';
                }
                toast(data.message || '备份完成');
            } else {
                toast(data.message || '备份失败', 'error');
            }
        } catch (error) {
            toast('备份请求失败：' + error.message, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.classList.remove('opacity-60');
                button.innerHTML = originalHtml;
            }
        }
    };

    window.copyUpgradeChecklist = async function (button) {
        const ok = await copyText(UPGRADE_CHECKLIST);
        if (ok) {
            flashCopied(button);
            toast('升级清单已复制');
        } else {
            toast('复制失败，请手动复制', 'warning');
        }
    };

    // ------------------------------------------------------------------
    // 3. 首次启动引导
    // ------------------------------------------------------------------

    function isDismissed() {
        try {
            return localStorage.getItem(DISMISS_KEY) === '1';
        } catch (error) {
            return false;
        }
    }

    function updateOnboardingEnvBadge(env) {
        const badge = $('onboardingEnvBadge');
        if (!badge || !env) return;
        const latexOk = !!(env.latex && env.latex.available);
        if (latexOk) {
            badge.textContent = '导出 PDF 环境已就绪';
        } else {
            badge.textContent = '提示：导出 PDF 还需安装 LaTeX（点此查看）';
        }
        badge.classList.remove('hidden');
        badge.style.cursor = 'pointer';
        badge.onclick = function () {
            window.openDepGuideModal('latex');
        };
    }

    function setKeyState(settings) {
        const stateEl = $('onboardingKeyState');
        if (!stateEl) return;
        const keys = ['deepseek_key', 'siliconflow_key', 'ali_bailian_key', 'zhongzhan_gpt_key'];
        const configured = keys.some(function (key) {
            return String((settings && settings[key]) || '').trim().length > 0;
        });
        if (configured) {
            stateEl.textContent = '已配置 ✓';
            stateEl.className = 'text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-50 dark:bg-emerald-500/20 text-emerald-600 dark:text-emerald-300';
        } else {
            stateEl.textContent = '未配置';
            stateEl.className = 'text-[9px] px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-300';
        }
    }

    window.refreshOnboarding = async function () {
        const card = $('onboardingCard');
        if (!card) return;
        if (isDismissed()) {
            card.classList.add('hidden');
            return;
        }

        let total = null;
        try {
            const stats = await getJson('/api/stats');
            total = typeof stats.total_count === 'number' ? stats.total_count : null;
        } catch (error) {
            total = null;
        }

        // 题库已有内容 → 引导任务完成，不再打扰
        if (total === null || total > 0) {
            card.classList.add('hidden');
            return;
        }

        card.classList.remove('hidden');

        // 示例题可用数量（文件缺失时后端返回 0，按钮降级为"导入试卷"提示）
        try {
            const sample = await getJson('/api/sample-questions');
            const countEl = $('onboardingSampleCount');
            const sampleBtn = $('onboardingSampleBtn');
            if (countEl && typeof sample.available === 'number') {
                countEl.textContent = String(sample.available);
            }
            if (sampleBtn && (!sample.available || sample.available <= 0)) {
                sampleBtn.disabled = true;
                sampleBtn.classList.add('opacity-50');
                sampleBtn.textContent = '示例题不可用';
            }
        } catch (error) {
            /* 忽略：不影响主流程 */
        }

        try {
            const settings = await getJson('/api/settings');
            setKeyState(settings);
        } catch (error) {
            /* 忽略 */
        }

        if (!envCache) {
            await window.fetchEnvironmentStatus();
        }
        updateOnboardingEnvBadge(envCache);
    };

    window.dismissOnboarding = function (persist) {
        const card = $('onboardingCard');
        if (card) card.classList.add('hidden');
        if (persist) {
            try {
                localStorage.setItem(DISMISS_KEY, '1');
            } catch (error) {
                /* 忽略隐私模式下写入失败 */
            }
            toast('已隐藏引导。想恢复：设置 → 关于 → 重新显示新手引导');
        }
    };

    window.resetOnboarding = function () {
        try {
            localStorage.removeItem(DISMISS_KEY);
        } catch (error) {
            /* 忽略 */
        }
        toast('已重新开启新手引导');
        window.refreshOnboarding();
    };

    function refreshBankViewsAfterImport() {
        if (typeof window.loadQuestions === 'function') {
            try {
                window.loadQuestions();
                return;
            } catch (error) {
                /* 落入兜底刷新 */
            }
        }
        setTimeout(function () {
            window.location.reload();
        }, 1200);
    }

    window.importSampleQuestions = async function () {
        const button = $('onboardingSampleBtn');
        if (button && button.disabled) return;
        const originalHtml = button ? button.innerHTML : '';
        if (button) {
            button.disabled = true;
            button.classList.add('opacity-60');
            button.innerHTML = '<i class="fa-solid fa-circle-notch animate-spin text-[9px]"></i> 载入中...';
        }
        try {
            const body = new URLSearchParams();
            body.append('force', 'false');
            const data = await getJson('/api/sample-questions/import', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: body.toString()
            });
            if (data.status === 'success' || data.status === 'skipped') {
                toast(data.message || '示例题目已载入');
                window.dismissOnboarding(false);
                refreshBankViewsAfterImport();
            } else {
                toast(data.message || '示例题目载入失败', 'error');
            }
        } catch (error) {
            toast('示例题目载入失败：' + error.message, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.classList.remove('opacity-60');
                button.innerHTML = originalHtml;
            }
        }
    };

    // ------------------------------------------------------------------
    // 初始化
    // ------------------------------------------------------------------

    document.addEventListener('DOMContentLoaded', function () {
        installLatexFailureWatcher();
        window.refreshOnboarding();
        // showToast 由 api.js 定义，若加载顺序意外变化则稍后重试安装观察者
        setTimeout(installLatexFailureWatcher, 1200);
    });
})();
