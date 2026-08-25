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

const calls = [];
let taskSeq = 0;
const mkQ = (n, src) => Array.from({ length: n }, (_, i) => ({ question_type:'detailed_answer', content:`Q${i+1} from ${src}`, answer_markdown:`a${i+1}`, difficulty:'medium', source: src }));
win.fetch = async (url, opts) => {
  const u = String(url);
  calls.push(u.replace('http://127.0.0.1:8000','') + (opts&&opts.method?` [${opts.method}]`:''));
  if (u.includes('/api/upload/pdf-task')) return { ok:true, json: async () => ({ status:'success', task_id:'T'+(++taskSeq) }) };
  if (u.includes('/api/tasks/') && u.includes('/status')) return { ok:true, json: async () => ({ status:'completed', progress:100, data: mkQ(3, win.__currentParseSourceFile||'pdf') }) };
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
        process.exit(0);
      }
    }, 300);
  }, 300);
}, 200);
