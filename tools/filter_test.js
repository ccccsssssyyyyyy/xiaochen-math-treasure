// 回归测试：拆解结果审查筛选（未导入 / 已导入 / 全部）
// 复用 multifile_test_pdf.js 的 jsdom 引导结构，直接驱动真实渲染与筛选函数。
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
console.log('[harness] injected; renderParsedQuestionsList=', typeof win.renderParsedQuestionsList, '| setReviewFilter=', typeof win.setReviewFilter);

function visibleCards() { return Array.from(doc.querySelectorAll('[id^="parsed-card-"]')).filter(c => !c.classList.contains('hidden')); }
function txt(id) { const el = doc.getElementById(id); return el ? el.textContent.trim() : 'MISSING'; }
const results = [];
function check(name, cond, extra) { results.push({ name, pass: !!cond, extra }); console.log((cond?'PASS':'FAIL')+': '+name+(extra!==undefined?(' -> '+extra):'')); }

setTimeout(() => {
  try {
    // 1) 播种 4 道题：0,1 未导入；2,3 已导入。单文件分组。
    win.eval(`
      parsedQuestionsData.length = 0;
      parsedFileGroups.length = 0;
      parsedFileGroups.push({ name:'doc1.pdf', startIndex:0, count:4 });
      const mk = (saved) => ({ question_type:'single_choice', difficulty:'normal', saved:saved, source_file:'doc1.pdf', content:'x', answer_markdown:'y' });
      parsedQuestionsData.push(mk(false), mk(false), mk(true), mk(true));
    `);
    win.__parsedReviewFilter = 'unimported';
    win.renderParsedQuestionsList(win.eval('parsedQuestionsData'));

    // 2) 默认「未导入」视图：应只显示 0,1 两题
    check('unimported 视图可见题数=2', visibleCards().length === 2, visibleCards().length);
    check('计数徽标 未导入=2', txt('filterCountUnimported') === '2', txt('filterCountUnimported'));
    check('计数徽标 已导入=2', txt('filterCountImported') === '2', txt('filterCountImported'));
    check('计数徽标 全部=4', txt('filterCountAll') === '4', txt('filterCountAll'));
    check('共 N 题 徽标=2', txt('parsedCountBadge') === '共 2 题', txt('parsedCountBadge'));
    check('getCheckedUnsavedIndices=2', win.getCheckedUnsavedIndices().length === 2, win.getCheckedUnsavedIndices().length);
    check('已选 X/Y 徽标=已选 2 / 2 题', txt('selectedCountBadge') === '已选 2 / 2 题', txt('selectedCountBadge'));

    // 3) 切到「已导入」：只显示 2,3，且无可选未导入
    win.setReviewFilter('imported');
    check('imported 视图可见题数=2', visibleCards().length === 2, visibleCards().length);
    check('imported 时无可勾选未导入', win.getCheckedUnsavedIndices().length === 0, win.getCheckedUnsavedIndices().length);
    check('imported 时 已选 0/0', txt('selectedCountBadge') === '已选 0 / 0 题', txt('selectedCountBadge'));

    // 4) 切到「全部」：4 题全可见
    win.setReviewFilter('all');
    check('all 视图可见题数=4', visibleCards().length === 4, visibleCards().length);

    // 5) 模拟把第 0 题标记为已导入并重算：已导入变 3，未导入变 1
    win.setReviewFilter('unimported');
    win.eval('parsedQuestionsData[0].saved = true;');
    win.applyReviewFilter();
    check('标记1题后 未导入=1', txt('filterCountUnimported') === '1', txt('filterCountUnimported'));
    check('标记1题后 已导入=3', txt('filterCountImported') === '3', txt('filterCountImported'));
    check('标记1题后 未导入视图可见=1', visibleCards().length === 1, visibleCards().length);

    // 6) 全选只作用于当前可见（未导入）题：应只勾中 1 个
    win.toggleSelectAllParsed(true);
    check('全选(未导入视图)勾中=1', win.getCheckedUnsavedIndices().length === 1, win.getCheckedUnsavedIndices().length);
  } catch (e) {
    console.error('TEST EXCEPTION:', e.stack || e.message);
    results.push({ name: 'exception', pass: false, extra: e.message });
  }
  const failed = results.filter(r => !r.pass);
  console.log('=== SUMMARY ===', 'passed:', results.length - failed.length, '/', results.length);
  process.exit(failed.length === 0 ? 0 : 1);
}, 300);
