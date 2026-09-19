/**
 * 全站唯一的 KaTeX 渲染入口。
 *
 * 为什么要有这个文件：`delimiters` 那 4 行配置原先散落在 editor / import /
 * paper / mistake 四个文件里，共 13 份字面量副本。其中 paper.js 已经在同一个
 * 文件里抄错过一次 —— 定义了 PAPER_KATEX_DELIMS 常量，500 行之外又手抄了一份。
 * 抄错不会报错，只会让同一道题在两处排出不同的样子（$$ 与 \[ \] 是行内还是
 * 独占一行），所以这里把它收敛成两套语义，其余调用点一律引用。
 *
 *   DELIMS_INLINE   窄栏用（左侧题库卡片），$$ 与 \[ \] 也按行内排版
 *   DELIMS_DISPLAY  正文预览 / 试卷 / 错题用，$$ 与 \[ \] 独占一行
 *
 * tests/test_katex_delims_contract.py 会拦住新的裸配置字面量，别在别处重抄。
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

    window.MathRender = {
        DELIMS_INLINE: DELIMS_INLINE,
        DELIMS_DISPLAY: DELIMS_DISPLAY,
        delimitersFor: delimitersFor,
        render: render
    };
})();
