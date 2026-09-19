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

function loadBaseModules(sandbox) {
  BASE_MODULES.forEach((name) => {
    const source = fs.readFileSync(path.join(JS_DIR, name), 'utf8');
    vm.runInContext(source, sandbox, { filename: name });
  });
}

module.exports = { loadBaseModules, JS_DIR, BASE_MODULES };
