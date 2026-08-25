// 回归测试：已导入文档免重复拆解 + 仍要拆解兜底
// 验证：(1) 查库命中“已导入”的文件被 skipped 且明显提示；(2) 其余文件正常拆解；
//      (3) 调用 __forceReparseByName 可强制重新拆解被跳过的文件。
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

const calls = [];
let taskSeq = 0;
// 已导入库的文件名集合（精确匹配）
const IMPORTED = { 'paperA.pdf': 5 };
const mkQ = (n, src) => Array.from({ length: n }, (_, i) => ({ question_type:'detailed_answer', content:`Q${i+1} from ${src}`, answer_markdown:`a${i+1}`, difficulty:'medium', source: src }));
const IMG_COUNT = { 'paperA.pdf': 2, 'paperB.pdf': 3, 'paperC.pdf': 4 };
win.fetch = async (url, opts) => {
  const u = String(url);
  calls.push(u.replace('http://127.0.0.1:8000','') + (opts&&opts.method?` [${opts.method}]`:''));
  if (u.includes('/api/documents/imported')) {
    const m = u.match(/[?&]name=([^&]+)/);
    const name = m ? decodeURIComponent(m[1]) : '';
    const imported = !!IMPORTED[name];
    return { ok:true, json: async () => ({ imported, count: imported ? IMPORTED[name] : 0 }) };
  }
  if (u.includes('/api/upload/pdf-task')) return { ok:true, json: async () => ({ status:'success', task_id:'T'+(++taskSeq) }) };
  if (u.includes('/api/tasks/') && u.includes('/status')) {
    const src = win.__currentParseSourceFile || 'pdf';
    const n = IMG_COUNT[src] || 2;
    const imgs = Array.from({ length: n }, (_, i) => `http://x/${src}_${i+1}.png`);
    return { ok:true, json: async () => ({ status:'completed', progress:100, page_images: imgs, data: mkQ(3, src) }) };
  }
  return { ok:true, json: async () => ({ status:'success' }) };
};
win.addEventListener('error', e => console.error('[window.error]', (e.error&&(e.error.stack||e.error.message))||e.message));
win.addEventListener('unhandledrejection', e => console.error('[unhandledrejection]', (e.reason&&(e.reason.stack||e.reason.message))||e.reason));

for (const code of codes) { try { doc.body.appendChild(Object.assign(doc.createElement('script'), { textContent: code })); } catch (e) { console.error('[inject]', e.message); } }
console.log('[harness] injected; startImportParseClick=', typeof win.startImportParseClick, '| __forceReparseByName=', typeof win.__forceReparseByName);

const assert = (cond, label) => { console.log((cond?'✅':'❌')+' '+label); if(!cond) process.exitCode = 1; return cond; };

setTimeout(() => {
  const texInput = doc.getElementById('texFileInput');
  const fA = new win.File(['%PDF-1.4 dummy'], 'paperA.pdf', { type:'application/pdf' });
  const fB = new win.File(['%PDF-1.4 dummy'], 'paperB.pdf', { type:'application/pdf' });
  const fC = new win.File(['%PDF-1.4 dummy'], 'paperC.pdf', { type:'application/pdf' });
  Object.defineProperty(texInput, 'files', { value:[fA,fB,fC], configurable:true });
  texInput.dispatchEvent(new win.Event('change'));

  setTimeout(() => {
    try { win.startImportParseClick(); } catch(e){ console.error('start', e); }
    let w = 0; const iv = setInterval(() => { w += 300;
      if (w >= 15000) { clearInterval(iv);
        const snap = win.__pendingFilesSnapshot || [];
        const byName = Object.fromEntries(snap.map(f => [f.name, f.status]));
        console.log('=== 初步结果（已导入文档应被跳过）===');
        console.log('pendingFiles 状态:', JSON.stringify(byName));
        console.log('pendingFiles 错误明细:', JSON.stringify(snap.map(f=>({name:f.name,status:f.status,error:f.error||null}))));
        const pdfTaskCalls = calls.filter(c=>c.includes('pdf-task')).length;
        const docImportCalls = calls.filter(c=>c.includes('/api/documents/imported')).length;
        console.log('pdf-task 调用数（期望仅 B、C = 2）:', pdfTaskCalls);
        console.log('/api/documents/imported 调用数（期望 3）:', docImportCalls);
        const okInit = assert(byName['paperA.pdf'] === 'skipped', 'paperA.pdf 已导入 → 被跳过(skipped)')
          && assert(byName['paperB.pdf'] === 'done', 'paperB.pdf 未导入 → 正常拆解(done)')
          && assert(byName['paperC.pdf'] === 'done', 'paperC.pdf 未导入 → 正常拆解(done)')
          && assert(pdfTaskCalls === 2, '仅未导入文件发起拆解请求（2 个 pdf-task）')
          && assert(docImportCalls === 3, '每个文件都先查库（3 次 imported 查询）');

        // 阶段二：触发“仍要拆解”强制重新拆解 paperA.pdf
        console.log('=== 触发 __forceReparseByName(paperA.pdf) ===');
        const before = calls.filter(c=>c.includes('pdf-task')).length;
        win.__forceReparseByName('paperA.pdf');
        let w2 = 0; const iv2 = setInterval(() => { w2 += 300;
          if (w2 >= 12000) { clearInterval(iv2);
            const snap2 = win.__pendingFilesSnapshot || [];
            const byName2 = Object.fromEntries(snap2.map(f => [f.name, f.status]));
            const after = calls.filter(c=>c.includes('pdf-task')).length;
            console.log('=== 强制重新拆解后 ===');
            console.log('pendingFiles 状态:', JSON.stringify(byName2));
            console.log('pdf-task 调用数（期望 +1 = 3）:', after);
            const okForce = assert(byName2['paperA.pdf'] === 'done', 'paperA.pdf 经「仍要拆解」后被成功重新拆解(done)')
              && assert(after === before + 1, '强制拆解确实发起了新的 pdf-task 请求');
            console.log('\n=== 总结 ===');
            console.log('初步跳过逻辑:', okInit ? 'PASS' : 'FAIL');
            console.log('仍要拆解兜底:', okForce ? 'PASS' : 'FAIL');
            process.exit((okInit && okForce) ? 0 : 1);
          }
        }, 300);
      }
    }, 300);
  }, 300);
}, 200);
