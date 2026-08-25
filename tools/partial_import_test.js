// 回归测试：方案B —— 部分文件完成即可导入。
// 场景：fileA.pdf 已拆解完成（3 道），fileB.pdf 仍在拆解中（卡住/processing）。
// 阶段一（队列还有未拆完文件）点“导入选中”，应自动只导入已出现的 fileA 的 3 道题，
// 不导入尚未出现的 fileB 的题（题目还没拆解出来）。
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

// 文件B 故意不超时（100s），让它一直卡在 processing，模拟“卡住但尚未超时的中途状态”
win.__PARSE_TIMEOUT_MS = 100000;

const calls = [];
let taskSeq = 0;
const mkQ = (n, src) => Array.from({ length: n }, (_, i) => ({ question_type:'detailed_answer', content:`Q${i+1} from ${src}`, answer_markdown:`a${i+1}`, difficulty:'medium', source: src }));
win.fetch = async (url, opts) => {
  const u = String(url);
  calls.push(u.replace('http://127.0.0.1:8000','') + (opts&&opts.method?` [${opts.method}]`:''));
  if (u.includes('/api/documents/imported')) {
    return { ok:true, json: async () => ({ imported:false, count:0 }) };
  }
  if (u.includes('/api/upload/pdf-task')) return { ok:true, json: async () => ({ status:'success', task_id:'T'+(++taskSeq) }) };
  if (u.includes('/api/tasks/') && u.includes('/status')) {
    const m = u.match(/tasks\/(T\d+)\/status/);
    const id = m ? m[1] : '';
    if (id === 'T1') {
      // fileA 正常完成，3 道题
      return { ok:true, json: async () => ({ status:'completed', progress:100, page_images:['http://x/a1.png'], data: mkQ(3, 'fileA.pdf') }) };
    }
    // fileB 卡死（一直 processing）
    return { ok:true, json: async () => ({ status:'processing', progress:50, page_images: [] }) };
  }
  if (u.includes('/api/questions')) {
    return { ok:true, json: async () => ({ status:'success', question: { id: 1 } }) };
  }
  return { ok:true, json: async () => ({ status:'success' }) };
};
win.addEventListener('error', e => console.error('[window.error]', (e.error&&(e.error.stack||e.error.message))||e.message));
win.addEventListener('unhandledrejection', e => console.error('[unhandledrejection]', (e.reason&&(e.reason.stack||e.reason.message))||e.reason));

for (const code of codes) { try { doc.body.appendChild(Object.assign(doc.createElement('script'), { textContent: code })); } catch (e) { console.error('[inject]', e.message); } }
console.log('[harness] injected; startImportParseClick=', typeof win.startImportParseClick, '| saveAllParsedQuestions=', typeof win.saveAllParsedQuestions);

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
      if (w >= 3000) { clearInterval(iv);
        const snap = win.__pendingFilesSnapshot || [];
        const byName = Object.fromEntries(snap.map(f => [f.name, f.status]));
        console.log('=== 部分完成状态 ===');
        console.log('pendingFiles 状态:', JSON.stringify(byName));
        const checkboxCount = doc.querySelectorAll('.card-select-checkbox').length;
        console.log('[debug] checkboxes=', checkboxCount, '| cards(class=parsed-card)=', doc.querySelectorAll('.parsed-card').length);
        // fileA 应 done，fileB 应 parsing（卡住）
        if (byName['fileA.pdf'] !== 'done' || byName['fileB.pdf'] !== 'parsing') {
          console.log('❌ 前置条件未满足（fileA 未完成 / fileB 未在拆），跳过导入测试');
          process.exit(1);
        }
        const saveBtn = doc.getElementById('saveAllParsedBtn');
        // 验证1：方案B放宽——阶段一（fileA完成/fileB拆解中）导入按钮应已启用
        const ok1 = assert(saveBtn && saveBtn.disabled === false, '阶段一（fileA完成/fileB拆解中）导入按钮已启用（方案B放宽禁用）');
        // 验证2：列表仅含已完成文件A的题（3），fileB题尚未出现 → 中途导入只会导已拆完的
        const ok2 = assert(checkboxCount === 3, '阶段一审查列表仅含已完成的 fileA 的 3 道题（fileB 未拆完，题未进入列表）');
        // 验证3：取消勾选后点击导入，应自动全选已出现的题（方案B中途导入核心逻辑）
        if (typeof win.toggleSelectAllParsed === 'function') win.toggleSelectAllParsed(false);
        const checkedBefore = [...doc.querySelectorAll('.card-select-checkbox')].filter(c=>c.checked).length;
        assert(checkedBefore === 0, '取消勾选后 checked=0（前置成立）');
        try { if (typeof win.saveAllParsedQuestions === 'function') win.saveAllParsedQuestions(); }
        catch(e){ console.error('import', e); }
        const checkedAfter = [...doc.querySelectorAll('.card-select-checkbox')].filter(c=>c.checked).length;
        const ok3 = assert(checkedAfter === 3, '点击导入后自动全选已拆解完成文件A的 3 道题（方案B中途导入）');
        console.log('\n=== 中途导入回归（jsdom 可证实部分）===', (ok1 && ok2 && ok3) ? 'PASS' : 'FAIL');
        console.log('（注：真实保存 POST /api/questions 依赖复杂卡片 DOM，jsdom 渲染受限无法复现，需在浏览器验证）');
        process.exit((ok1 && ok2 && ok3) ? 0 : 1);
      }
    }, 300);
  }, 300);
}, 200);
