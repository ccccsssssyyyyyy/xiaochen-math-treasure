/**
 * 全站唯一的渲染入口 —— 两件事都收在这里，别处不许重抄：
 *
 * 1. **KaTeX**：`delimiters` 那 4 行配置原先散落在 editor / import / paper /
 *    mistake 四个文件里，共 13 份字面量副本。其中 paper.js 已经在同一个文件里
 *    抄错过一次 —— 定义了 PAPER_KATEX_DELIMS 常量，500 行之外又手抄了一份。
 *    抄错不会报错，只会让同一道题在两处排出不同的样子（$$ 与 \[ \] 是行内还是
 *    独占一行），所以这里把它收敛成两套语义，其余调用点一律引用。
 *
 *      DELIMS_INLINE   窄栏用（左侧题库卡片），$$ 与 \[ \] 也按行内排版
 *      DELIMS_DISPLAY  正文预览 / 试卷 / 错题用，$$ 与 \[ \] 独占一行
 *
 *    tests/test_katex_delims_contract.py 会拦住新的裸配置字面量。
 *
 * 2. **题面正文 → HTML**（2026-09-21 补上，见文件下半部的 `renderQuestionBody`）：
 *    `![alt](url)` 内联图那套语法原先四处各写一份正则与安全判断，redo.js 那份
 *    漏了整条分支 —— 同一道题「导出的 PDF 上有图、重做页印出一串 markdown」。
 *    现在 `INLINE_IMAGE_RE` 只有这一份，业务模块只负责各自的版式。
 *    tests/test_redo_frontend.py::test_inline_figure_syntax_has_single_definition
 *    会拦住新抄的正则。
 */
(function () {
    'use strict';

    const DELIMS_INLINE = [
        { left: '$$', right: '$$', display: false },
        { left: '$', right: '$', display: false },
        { left: '\\(', right: '\\)', display: false },
        { left: '\\[', right: '\\]', display: false }
    ];

    const DELIMS_DISPLAY = [
        { left: '$$', right: '$$', display: true },
        { left: '$', right: '$', display: false },
        { left: '\\(', right: '\\)', display: false },
        { left: '\\[', right: '\\]', display: true }
    ];

    // KaTeX 抛错只影响排版，不该连带把调用方的整块渲染逻辑打挂，
    // 所以 throwOnError 与 try/catch 都在这里收口，调用点不必再各写一遍。
    function delimitersFor(mode) {
        return mode === 'inline' ? DELIMS_INLINE : DELIMS_DISPLAY;
    }

    function render(target, mode) {
        if (!target) return false;
        if (typeof window.renderMathInElement !== 'function') return false;
        try {
            window.renderMathInElement(target, {
                delimiters: delimitersFor(mode),
                throwOnError: false
            });
            return true;
        } catch (error) {
            console.error('[MathRender] KaTeX 渲染失败:', error);
            return false;
        }
    }

    // ---------------------------------------------------------------- 题面正文
    //
    // 为什么题面渲染也必须收口在这里（2026-09-21）：`![alt](url)` 这套内联图语法
    // 原先在 editor / paper / mistake / redo 四处各有一份正则和一份安全判断，其中
    // redo 漏了整条分支 —— 结果是同一道题「导出的 PDF 上有图、重做页上印出一串
    // markdown」。漏的不是图，是「哪些语法算图」这个决定被抄了四遍。
    //
    // 现在只留这一份正则与这一份安全判断，四处调用点只负责各自的**版式**
    // （要不要移除按钮、图放右侧还是下方、要不要能点开大图）。

    /** 唯一的「什么算一张内联图」定义。别在别处重抄。 */
    const INLINE_IMAGE_RE = /!\[([^\]]*)\]\(\s*([^)\s]+)\s*\)/g;

    const FILLIN_HTML = '<span class="inline-block min-w-[3rem] border-b border-slate-400">&nbsp;</span>';
    const PAREN_HTML = '<span class="inline-block">&nbsp;（&nbsp;&nbsp;&nbsp;）</span>';

    // 占位符用控制字符包裹：esc 只转义 & < > " '，不会碰到它，
    // 所以「先摘图 → 再转义 → 最后换回 <img>」这条顺序不会把 URL 二次转义。
    const FIGURE_TOKEN_RE = /\u0000MRIMG(\d+)\u0000/g;

    function escapeText(value) {
        const guard = window.MathBankSafe && window.MathBankSafe.escapeText;
        if (guard) return guard(value);
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    }

    function escapeAttribute(value) {
        const guard = window.MathBankSafe && window.MathBankSafe.escapeAttribute;
        if (guard) return guard(value);
        return escapeText(value).replace(/\r?\n/g, ' ');
    }

    /** 过不了安全校验的 URL 一律返回 ''，调用点自己决定「留字」还是「抹掉」。 */
    function safeImageUrl(value) {
        const guard = window.MathBankSafe && window.MathBankSafe.safeImageUrl;
        return guard ? guard(value) : '';
    }

    /**
     * 按出现顺序遍历正文里的内联图，把每一处换成 ``build`` 的返回值。
     * 返回本次**真正渲染出来**的安全 URL（去重、保持顺序），供调用点做配图去重。
     */
    function replaceInlineFigures(text, build) {
        const urls = [];
        let index = -1;
        const html = String(text == null ? '' : text).replace(INLINE_IMAGE_RE, function (match, alt, rawUrl) {
            index += 1;
            const safeUrl = safeImageUrl(rawUrl);
            const ctx = { index: index, alt: String(alt || ''), rawUrl: rawUrl, safeUrl: safeUrl, match: match };
            const piece = build(ctx);
            if (safeUrl && piece !== match && urls.indexOf(safeUrl) === -1) urls.push(safeUrl);
            return piece;
        });
        return { html: html, urls: urls, count: index + 1 };
    }

    /** 只要 URL 列表（去重、按出现顺序）。 */
    function inlineFigureUrls(text) {
        return replaceInlineFigures(text, function (ctx) { return ctx.match; }).urls;
    }

    /** 把图从正文里摘掉，交给版式层单独摆放（paper.js 的右侧/下方配图块）。 */
    function stripInlineFigures(text) {
        const out = replaceInlineFigures(text, function () { return ''; });
        return { html: out.html, urls: out.urls, count: out.count };
    }

    /**
     * 题面正文 → HTML。mistake.js 与 redo.js 共用这一份：
     * choices 环境 → 选项列表、\fillin → 下划线空、\paren → 括号、\n → 折行、
     * 内联图 → <img>（版式由 opts.imageBuilder 决定）。
     *
     * 图在**转义之前**摘出来，再以占位符穿过转义，最后才拼 <img> ——
     * 否则「先转义整段、再对 URL 转义一次」会把 `&` 变成 `&amp;amp;`，图直接 404。
     */
    function renderQuestionBody(content, opts) {
        opts = opts || {};
        let text = String(content == null ? '' : content).trim();
        const figures = [];
        text = text.replace(INLINE_IMAGE_RE, function (match, alt, rawUrl) {
            figures.push({ match: match, alt: String(alt || ''), rawUrl: rawUrl });
            return '\u0000MRIMG' + (figures.length - 1) + '\u0000';
        });

        const choiceItems = [];
        text = text.replace(/\\begin\{choices\}([\s\S]*?)\\end\{choices\}/g, function (_m, inner) {
            const items = inner.split(/\\item\b/)
                .map(function (piece) { return piece.trim(); })
                .filter(function (piece) { return piece.length > 0; });
            choiceItems.push.apply(choiceItems, items);
            return '';
        }).trim();

        let html = escapeText(text)
            .replace(/\\fillin\b/g, FILLIN_HTML)
            .replace(/\\paren\b/g, PAREN_HTML)
            .replace(/\n{2,}/g, '<span class="block h-2"></span>')
            .replace(/\n/g, '<br>');

        const urls = [];
        // 选项里也可能带图（`\item ![](a.png)`），所以换回 <img> 这一步要能复用，
        // 不能只作用在题干上 —— 否则选项里会留下一串控制字符占位符。
        const restoreFigures = function (str) {
            return String(str).replace(FIGURE_TOKEN_RE, function (_m, ordinal) {
                const fig = figures[Number(ordinal)];
                if (!fig) return '';
                const safeUrl = safeImageUrl(fig.rawUrl);
                if (!safeUrl) {
                    // 过不了校验的原样留着：显示一串 markdown 顶多难看，
                    // 静默抹掉会让老师以为这题本来就没配图。
                    return opts.dropUnsafe === true ? '' : escapeText(fig.match);
                }
                if (urls.indexOf(safeUrl) === -1) urls.push(safeUrl);
                const ctx = {
                    index: Number(ordinal),
                    alt: fig.alt,
                    rawUrl: fig.rawUrl,
                    safeUrl: safeUrl,
                    match: fig.match
                };
                if (typeof opts.imageBuilder === 'function') return opts.imageBuilder(ctx);
                return '<span class="block my-1.5"><img src="' + escapeAttribute(safeUrl) +
                    '" alt="' + escapeAttribute(fig.alt || '题图') + '" loading="lazy" decoding="async" class="' +
                    (opts.figureClass || '') + '"></span>';
            });
        };
        html = restoreFigures(html);

        // 选项块排在最后，所以「缺图占位符」这类属于**题干**的标记必须在这里插 ——
        // 否则它会被挤到 A./B./C./D. 后面，看着像选项的一部分。
        if (typeof opts.beforeChoices === 'function') html = opts.beforeChoices(html);

        if (choiceItems.length) {
            const letters = 'ABCDEFGH';
            html += '<div class="mt-1.5 grid grid-cols-1 gap-0.5 text-[12px]' +
                (opts.choiceClass ? ' ' + opts.choiceClass : '') + '">' +
                choiceItems.map(function (item, index) {
                    return '<div><b class="text-slate-400">' + (letters[index] || (index + 1)) +
                        '.</b> ' + restoreFigures(escapeText(item)) + '</div>';
                }).join('') + '</div>';
        }

        return { html: html, urls: urls, count: figures.length };
    }

    window.MathRender = {
        DELIMS_INLINE: DELIMS_INLINE,
        DELIMS_DISPLAY: DELIMS_DISPLAY,
        delimitersFor: delimitersFor,
        render: render,
        // 题面正文
        INLINE_IMAGE_RE: INLINE_IMAGE_RE,
        escapeText: escapeText,
        escapeAttribute: escapeAttribute,
        safeImageUrl: safeImageUrl,
        replaceInlineFigures: replaceInlineFigures,
        inlineFigureUrls: inlineFigureUrls,
        stripInlineFigures: stripInlineFigures,
        renderQuestionBody: renderQuestionBody
    };
})();
