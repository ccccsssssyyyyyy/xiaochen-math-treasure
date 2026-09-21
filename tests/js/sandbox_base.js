/**
 * 把「真实页面的脚本加载顺序」搬进 vm 沙箱。
 *
 * `static/index.html` 里 `math-render.js` 排在所有业务脚本之前，浏览器里它一定先就位；
 * 但各个检查脚本只把单个业务 JS 丢进沙箱，`window.MathRender` 就是 undefined，
 * 于是 `renderMathIn` 这类调用会在沙箱里抛 TypeError —— 那是**环境缺口**，
 * 不是被测代码的问题。这里补上同一批前置模块，让沙箱与浏览器看到同一套全局。
 *
 * 用法：
 *     vm.createContext(sandbox);
 *     require('./sandbox_base').loadBaseModules(sandbox);
 *     vm.runInContext(src, sandbox, { filename: 'mistake.js' });
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS_DIR = path.join(__dirname, '..', '..', 'static', 'js');

// 必须与 index.html 的 <script> 顺序一致：基础模块在前，业务模块在后
const BASE_MODULES = ['math-render.js'];

// 浏览器里 api.js 紧跟着 math-render.js 加载并提供 window.MathBankSafe，
// 沙箱里通常没有它 —— 而题面内联图的判定（math-render.js）要过 safeImageUrl。
// 缺了它，每张图都会被判成「不安全」从而退回 markdown 原文，夹具就会看到
// 「图渲染出来了」其实是**原文还在**。这里按 api.js 的同一份契约补上。
// 夹具自己放了更严格的桩时以夹具为准（先到先得，不覆盖）。
function installMathBankSafeFallback(sandbox) {
  if (sandbox.MathBankSafe) return;
  const escapeText = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  sandbox.MathBankSafe = {
    escapeText,
    escapeAttribute: (value) => escapeText(value).replace(/\r?\n/g, ' '),
    sanitizePlainText: (value) => String(value == null ? '' : value),
    safeImageUrl: (value) => {
      const raw = String(value == null ? '' : value).trim();
      if (!raw || /[\u0000-\u001F\u007F\\]/.test(raw)) return '';
      const path = raw.split('?')[0];
      if (!/^\/static\/(uploads|test_uploads)\//.test(path)) return '';
      if (path.split('/').includes('..')) return '';
      if (!/\.(png|jpe?g|gif|webp)$/i.test(path)) return '';
      return path;
    },
    safeClassList: (value, fallback = '') => String(value == null ? '' : value) || fallback
  };
}

function loadBaseModules(sandbox) {
  BASE_MODULES.forEach((name) => {
    const source = fs.readFileSync(path.join(JS_DIR, name), 'utf8');
    vm.runInContext(source, sandbox, { filename: name });
  });
  installMathBankSafeFallback(sandbox);
}

module.exports = { loadBaseModules, JS_DIR, BASE_MODULES };
