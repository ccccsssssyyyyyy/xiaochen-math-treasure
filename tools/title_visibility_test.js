// 回归测试：多文件队列模式下隐藏「第一步：试卷标题」输入框与 TeX 配套图片区
// 验证：(1) 仅选 1 个文件时标题区/图片区均显示；(2) 入队文件达到 ≥2 时两者隐藏。
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('/Users/ccsssy/.workbuddy/binaries/node/workspace/node_modules/jsdom');

const ROOT = '/Users/ccsssy/WorkBuddy/2026-08-24-00-26-36/math-question-bank';
const html = fs.readFileSync(path.join(ROOT, 'static/index.html'), 'utf8');
const fileNames = ['api', 'editor', 'ocr', 'import', 'paper'];
const codes = fileNames.map(n => fs.readFileSync(path.join(ROOT, 'static/js', `${n}.js`), 'utf8'));

const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://127.0.0.1:8000/', pretendToBeVisual: true });
const win = dom.window; const doc = win.document;
win.renderMathInElement = () => {}; win.marked = { parse: x => x || '' }; win.DOMPurify = { sanitize: x => x }; win.tailwind = { config: {} };
win.IntersectionObserver = class { observe(){} unobserve(){} disconnect(){} };
win.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} };
try { win.localStorage.setItem('local_token', 'test-token'); } catch (e) {}
win.local_token = 'test-token'; win.systemPreferParseModel = 'deepseek-chat';
win.systemMetadata = { question_types: [ {value:'single_choice',label:'单选题'},{value:'multi_choice',label:'多选题'},{value:'fill_in_blank',label:'填空题'},{value:'detailed_answer',label:'解答题'} ], difficulties: [{value:'easy',label:'易'},{value:'medium',label:'中'},{value:'hard',label:'难'}] };
win.MathBankModal = { open(){}, close(){} };

win.fetch = async () => ({ ok:true, json: async () => ({ status:'success' }) });
win.addEventListener('error', e => console.error('[window.error]', (e.error&&(e.error.stack||e.error.message))||e.message));
win.addEventListener('unhandledrejection', e => console.error('[unhandledrejection]', (e.reason&&(e.reason.stack||e.reason.message))||e.reason));

for (const code of codes) { try { doc.body.appendChild(Object.assign(doc.createElement('script'), { textContent: code })); } catch (e) { console.error('[inject]', e.message); } }
console.log('[harness] injected; enqueueFiles=', typeof win.enqueueFiles, '| renderFileQueue=', typeof win.renderFileQueue);

const assert = (cond, label) => { console.log((cond?'✅':'❌')+' '+label); if(!cond) process.exitCode = 1; return cond; };

setTimeout(() => {
  const texInput = doc.getElementById('texFileInput');
  const titleGroup = doc.getElementById('importTitleGroup');
  const texImages = doc.getElementById('texImagesSection');
  const fA = new win.File(['%PDF-1.4 dummy'], 'A.pdf', { type:'application/pdf' });
  const fB = new win.File(['%PDF-1.4 dummy'], 'B.pdf', { type:'application/pdf' });
  const fC = new win.File(['%PDF-1.4 dummy'], 'C.pdf', { type:'application/pdf' });
  if (!titleGroup) { console.error('❌ 找不到 #importTitleGroup'); process.exit(1); }

  // 场景1：仅选 1 个文件（单文件队列模式）
  Object.defineProperty(texInput, 'files', { value:[fA], configurable:true });
  texInput.dispatchEvent(new win.Event('change'));
  setTimeout(() => {
    const ok1 = assert(!titleGroup.classList.contains('hidden'), '单文件(1)模式：标题输入框显示');
    const ok1b = assert(!texImages || !texImages.classList.contains('hidden'), '单文件(1)模式：TeX图片区显示');

    // 场景2：再追加 2 个文件 → 队列达到 ≥2（多文件队列模式）
    Object.defineProperty(texInput, 'files', { value:[fB, fC], configurable:true });
    texInput.dispatchEvent(new win.Event('change'));
    setTimeout(() => {
      const ok2 = assert(titleGroup.classList.contains('hidden'), '多文件(≥2)模式：标题输入框隐藏');
      const ok2b = assert(!texImages || texImages.classList.contains('hidden'), '多文件(≥2)模式：TeX图片区隐藏');
      console.log('\n=== 总结 ===');
      console.log('标题区/图片区显隐切换:', (ok1 && ok1b && ok2 && ok2b) ? 'PASS' : 'FAIL');
      process.exit((ok1 && ok1b && ok2 && ok2b) ? 0 : 1);
    }, 300);
  }, 300);
}, 200);
