// 回归测试：单题录入 OCR 成功后，AI 分类结果自动填充录入表单
// 驱动真实 import.js 的 applyClassifyResultToEditor，断言各字段被正确填充。
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = require('path').resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8');
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
win.fetch = (url) => {
  if (typeof url === 'string' && url.indexOf('/api/categories') !== -1) {
    return Promise.resolve({ ok: true, json: async () => ({ '必修一': { '1. 集合': ['1.1 集合的概念', '1. 集合'] } }) });
  }
  return Promise.resolve({ ok: true, json: async () => ({ status: 'success' }) });
};
win.addEventListener('error', e => console.error('[window.error]', (e.error&&(e.error.stack||e.error.message))||e.message));
win.addEventListener('unhandledrejection', e => console.error('[unhandledrejection]', (e.reason&&(e.reason.stack||e.reason.message))||e.reason));

for (const code of codes) { try { doc.body.appendChild(Object.assign(doc.createElement('script'), { textContent: code })); } catch (e) { console.error('[inject]', e.message); } }
console.log('[harness] applyClassifyResultToEditor =', typeof win.applyClassifyResultToEditor);

// 预置教材树（同时写入词法全局 categoryTree 与 window.categoryTree，模拟 api.js 的真实同步）
// + 兜底 options，确保 populateCategoryDropdowns 与 select.value 设置都能命中。
win.eval(`
  categoryTree = { '必修一': { '1. 集合': ['1.1 集合的概念', '1. 集合'] } };
  window.categoryTree = categoryTree;
  [['editCompulsory','必修一'],['editChapter','1. 集合'],['editKnowledge','1.1 集合的概念'],['editKnowledge','1. 集合']]
    .forEach(([id,val]) => { const s=document.getElementById(id); const o=document.createElement('option'); o.value=val; o.textContent=val; s.appendChild(o); });
`);

const results = [];
function check(name, cond, extra) { results.push({ name, pass: !!cond, extra }); console.log((cond?'PASS':'FAIL')+': '+name+(extra!==undefined?(' -> '+extra):'')); }

setTimeout(() => {
  try {
    const data = {
      question_type: 'single_choice',
      difficulty: 'challenge',
      source: '2024全国高考真题',
      compulsory: '必修一',
      chapter: '1. 集合',
      category_knowledge: '1.1 集合的概念',
      knowledge_list: ['集合的运算', '子集'],
      solve_method: ['Venn 图法', '分类讨论']
    };
    win.applyClassifyResultToEditor(data);

    check('题型填充 single_choice', doc.getElementById('editQType').value === 'single_choice', doc.getElementById('editQType').value);
    check('难度填充 challenge', doc.getElementById('editDifficulty').value === 'challenge', doc.getElementById('editDifficulty').value);
    check('来源填充 2024全国高考真题', doc.getElementById('editSource').value === '2024全国高考真题', doc.getElementById('editSource').value);
    check('学段填充 必修一', doc.getElementById('editCompulsory').value === '必修一', doc.getElementById('editCompulsory').value);
    check('章节填充 1. 集合', doc.getElementById('editChapter').value === '1. 集合', doc.getElementById('editChapter').value);
    check('小节填充 1.1 集合的概念', doc.getElementById('editKnowledge').value === '1.1 集合的概念', doc.getElementById('editKnowledge').value);

    const kn = doc.getElementById('editKnowledgeTagInput');
    const knTags = kn._getTags().split(',').filter(Boolean);
    check('知识点标签数=2', knTags.length === 2, kn._getTags());
    check('知识点 chip DOM 渲染', doc.querySelectorAll('#editKnowledgeTagsChips > span').length === 2, doc.querySelectorAll('#editKnowledgeTagsChips > span').length);

    const sm = doc.getElementById('editSolveMethodTagInput');
    const smTags = sm._getTags().split(',').filter(Boolean);
    check('解题方法标签数=2', smTags.length === 2, sm._getTags());
    check('解题方法 chip DOM 渲染', doc.querySelectorAll('#editSolveMethodTagsChips > span').length === 2, doc.querySelectorAll('#editSolveMethodTagsChips > span').length);

    // 自定义标签(editTags) 不被 AI 覆盖
    doc.getElementById('editTags').value = '我的标记';
    win.applyClassifyResultToEditor(data);
    check('自定义标签未被 AI 覆盖', doc.getElementById('editTags').value === '我的标记', doc.getElementById('editTags').value);

    // 非法题型/难度应被忽略（不破坏当前选中）
    doc.getElementById('editQType').value = 'multi_choice';
    win.applyClassifyResultToEditor({ question_type: 'bogus', difficulty: 'weird', compulsory: '必修一', chapter: '1. 集合', category_knowledge: '1.1 集合的概念' });
    check('非法题型被忽略(保留 multi_choice)', doc.getElementById('editQType').value === 'multi_choice', doc.getElementById('editQType').value);
    check('非法难度被忽略(保留 challenge)', doc.getElementById('editDifficulty').value === 'challenge', doc.getElementById('editDifficulty').value);
  } catch (e) {
    console.error('TEST ERROR', e);
    results.push({ name: 'exception', pass: false, extra: e.message });
  }

  const passed = results.filter(r => r.pass).length;
  console.log(`\nSUMMARY: ${passed}/${results.length} passed`);
  if (passed !== results.length) {
    console.log('FAILED:', results.filter(r => !r.pass).map(r => r.name));
  }
  process.exit(passed === results.length ? 0 : 1);
}, 400);
