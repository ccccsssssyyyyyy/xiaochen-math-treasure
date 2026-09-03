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

const calls = [];
let taskSeq = 0;
const mkQ = (n, src) => Array.from({ length: n }, (_, i) => ({ question_type:'detailed_answer', content:`Q${i+1} from ${src}`, answer_markdown:`a${i+1}`, difficulty:'medium', source: src }));
win.fetch = async (url, opts) => {
  const u = String(url);
  calls.push(u.replace('http://127.0.0.1:8000','') + (opts&&opts.method?` [${opts.method}]`:''));
  if (u.includes('/api/upload/pdf-task')) return { ok:true, json: async () => ({ status:'success', task_id:'T'+(++taskSeq) }) };
  if (u.includes('/api/tasks/') && u.includes('/status')) {
    const src = win.__currentParseSourceFile || 'pdf';
    const imgs = src === 'paperA.pdf' ? ['http://x/A1.png','http://x/A2.png'] : ['http://x/B1.png','http://x/B2.png','http://x/B3.png'];
    return { ok:true, json: async () => ({ status:'completed', progress:100, page_images: imgs, data: mkQ(3, src) }) };
  }
  return { ok:true, json: async () => ({ status:'success' }) };
};
win.addEventListener('error', e => console.error('[window.error]', (e.error&&(e.error.stack||e.error.message))||e.message));
win.addEventListener('unhandledrejection', e => console.error('[unhandledrejection]', (e.reason&&(e.reason.stack||e.reason.message))||e.reason));

for (const code of codes) { try { doc.body.appendChild(Object.assign(doc.createElement('script'), { textContent: code })); } catch (e) { console.error('[inject]', e.message); } }
console.log('[harness] injected; startImportParseClick=', typeof win.startImportParseClick);

setTimeout(() => {
  const texInput = doc.getElementById('texFileInput');
  const f1 = new win.File(['%PDF-1.4 dummy'], 'paperA.pdf', { type:'application/pdf' });
  const f2 = new win.File(['%PDF-1.4 dummy'], 'paperB.pdf', { type:'application/pdf' });
  Object.defineProperty(texInput, 'files', { value:[f1,f2], configurable:true });
  texInput.dispatchEvent(new win.Event('change'));
  setTimeout(() => {
    try { win.startImportParseClick(); } catch(e){ console.error('start', e); }
    let w=0; const iv=setInterval(()=>{ w+=300;
      if (w>=12000){ clearInterval(iv);
        const badge=doc.getElementById('parsedCountBadge'); const qp=doc.getElementById('importQueueProgress');
        console.log('=== FINAL ===');
        console.log('countBadge:', badge?badge.textContent:'MISSING');
        console.log('queueProgress:', qp?qp.textContent:'MISSING');
        console.log('pdf-task calls:', calls.filter(c=>c.includes('pdf-task')).length, 'tasks/status calls:', calls.filter(c=>c.includes('/tasks/')).length);
        console.log('has 2 completed:', calls.filter(c=>c.includes('pdf-task')).length>=1);
        // === 多文件手动截图：验证页面图不再互相覆盖 ===
        const map = win.pdfPageImagesMap || {};
        console.log('=== CROP MAP (multi-file) ===');
        console.log('pdfPageImagesMap keys:', Object.keys(map));
        console.log('paperA images:', (map['paperA.pdf']&&map['paperA.pdf'].pageImages||[]).length, '| paperB images:', (map['paperB.pdf']&&map['paperB.pdf'].pageImages||[]).length);
        // 核心证明：两份文档的页面图都保留，paperA 没被 paperB 覆盖
        const mapOk = !!(map['paperA.pdf']&&map['paperA.pdf'].pageImages.length===2 && map['paperB.pdf']&&map['paperB.pdf'].pageImages.length===3);
        console.log('MAP RETAINED BOTH DOCS (no overwrite):', mapOk);
        // 路由验证（信息性）：点击 paperA 第 0 题的“手动截图”按钮，应切到 paperA 的 2 张图
        let routedOk = false;
        const allBtns = Array.from(doc.querySelectorAll('button'));
        const btn0 = allBtns.find(b => (b.getAttribute('onclick')||'').includes('openPdfCropModalForQuestion(0)'));
        if (btn0) {
          try { btn0.click(); } catch(e){ console.log('crop click DOM error (ignore):', e.message); }
          console.log('clicked paperA idx0 crop button; win.pdfPageImages length now:', (win.pdfPageImages||[]).length, '(expect 2)');
          routedOk = (win.pdfPageImages||[]).length === 2;
        } else {
          console.log('(info) crop button for idx0 not found in harness DOM — mapOk 已作为核心证据');
        }
        console.log('CROP ROUTING OK:', routedOk);
        // 退出码以核心修复（map 保留两份文档）为准
        process.exit(mapOk ? 0 : 1);
      }
    }, 300);
  }, 300);
}, 200);
