// 回归测试：方案B —— 某个文件拆解卡死时，超时保护应标记该文件 failed 并继续下一个，
// 而不是全盘卡死（曾有的 bug：后端任务一直 processing，前端永远等不到终态，
// 后续文件永远 pending、导入按钮禁用、已拆完的题也无法入库）。
// 通过 window.__PARSE_TIMEOUT_MS 把超时缩短到 200ms 快速触发。
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = require('path').resolve(__dirname, '..');
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

// 缩短超时，快速触发卡死判定
win.__PARSE_TIMEOUT_MS = 200;

const calls = [];
let taskSeq = 0;
win.fetch = async (url, opts) => {
  const u = String(url);
  calls.push(u.replace('http://127.0.0.1:8000','') + (opts&&opts.method?` [${opts.method}]`:''));
  if (u.includes('/api/documents/imported')) {
    return { ok:true, json: async () => ({ imported:false, count:0 }) };
  }
  if (u.includes('/api/upload/pdf-task')) return { ok:true, json: async () => ({ status:'success', task_id:'T'+(++taskSeq) }) };
  if (u.includes('/api/tasks/') && u.includes('/status')) {
    // 关键：模拟后端任务卡死，永远不返回 completed/error
    return { ok:true, json: async () => ({ status:'processing', progress:50, page_images: [] }) };
  }
  return { ok:true, json: async () => ({ status:'success' }) };
};
win.addEventListener('error', e => console.error('[window.error]', (e.error&&(e.error.stack||e.error.message))||e.message));
win.addEventListener('unhandledrejection', e => console.error('[unhandledrejection]', (e.reason&&(e.reason.stack||e.reason.message))||e.reason));

for (const code of codes) { try { doc.body.appendChild(Object.assign(doc.createElement('script'), { textContent: code })); } catch (e) { console.error('[inject]', e.message); } }
console.log('[harness] injected; startImportParseClick=', typeof win.startImportParseClick);

const assert = (cond, label) => { console.log((cond?'✅':'❌')+' '+label); if(!cond) process.exitCode = 1; return cond; };

setTimeout(() => {
  const texInput = doc.getElementById('texFileInput');
  const fA = new win.File(['%PDF-1.4 dummy'], 'fileA.pdf', { type:'application/pdf' });
  const fB = new win.File(['%PDF-1.4 dummy'], 'fileB.pdf', { type:'application/pdf' });
  Object.defineProperty(texInput, 'files', { value:[fA,fB], configurable:true });
  texInput.dispatchEvent(new win.Event('change'));

  setTimeout(() => {
    try { win.startImportParseClick(); } catch(e){ console.error('start', e); }
    let w = 0; const iv = setInterval(() => { w += 300;
      if (w >= 5000) { clearInterval(iv);
        const snap = win.__pendingFilesSnapshot || [];
        const byName = Object.fromEntries(snap.map(f => [f.name, f.status]));
        console.log('=== 卡死场景结果 ===');
        console.log('pendingFiles 状态:', JSON.stringify(byName));
        console.log('pendingFiles 错误明细:', JSON.stringify(snap.map(f=>({name:f.name,status:f.status,error:f.error||null}))));
        const pdfTaskCalls = calls.filter(c=>c.includes('pdf-task')).length;
        console.log('pdf-task 调用数（期望 2，两个都发起了拆解）:', pdfTaskCalls);
        // 核心断言：两个文件都进入了处理（没有被第一个卡死拖死），且最终都标记为 failed（超时保护生效）
        const ok = assert(byName['fileA.pdf'] === 'failed', 'fileA.pdf 卡死后被超时保护标记 failed')
          && assert(byName['fileB.pdf'] === 'failed', 'fileB.pdf 也进入处理并最终 failed（未被全盘卡死）')
          && assert(pdfTaskCalls === 2, '两个文件都发起了拆解请求（pdf-task=2，证明没有在第一个文件卡死）');
        console.log('\n=== 超时保护回归 ===', ok ? 'PASS' : 'FAIL');
        process.exit(ok ? 0 : 1);
      }
    }, 300);
  }, 300);
}, 200);
