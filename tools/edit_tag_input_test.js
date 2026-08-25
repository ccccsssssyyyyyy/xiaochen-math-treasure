// 回归测试：编辑面板「解题方法/知识点」多标签输入
// 锁定方案A的核心防护：
//  1) setupEditTagInput 把标签状态绑定到输入框元素自身 (input._getTags)，不再写全局变量；
//  2) 切换题目时 resetEditTagInputs() 同步清空，避免异步加载期间上一题标签被误存到本题。
// 复用 filter_test.js 的 jsdom 引导结构。
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
console.log('[harness] injected; setupEditTagInput=', typeof win.setupEditTagInput, '| resetEditTagInputs=', typeof win.resetEditTagInputs);

const results = [];
function check(name, cond, extra) { results.push({ name, pass: !!cond, extra }); console.log((cond?'PASS':'FAIL')+': '+name+(extra!==undefined?(' -> '+extra):'')); }
function T(id) { let el = doc.getElementById(id); if (!el) { el = doc.createElement('div'); el.id = id; doc.body.appendChild(el); } return el; }

setTimeout(() => {
  try {
    // 确保 4 个标签元素存在（编辑面板用）
    T('editKnowledgeTags'); T('editKnowledgeTagsChips'); T('editKnowledgeTagInput');
    T('editSolveMethodTags'); T('editSolveMethodTagsChips'); T('editSolveMethodTagInput');

    // 1) 载入题型 A：初始解题方法=「配凑法,换元法」
    win.setupEditTagInput('editSolveMethodTags', 'editSolveMethodTagsChips', 'editSolveMethodTagInput', '配凑法,换元法');
    const smInput = doc.getElementById('editSolveMethodTagInput');
    check('载入A: _getTags 返回「配凑法,换元法」', smInput._getTags() === '配凑法,换元法', smInput._getTags());
    check('载入A: 不再写入全局变量 window._editSolveMethodTags', win._editSolveMethodTags === undefined, typeof win._editSolveMethodTags);
    check('载入A: chip 数量=2', doc.getElementById('editSolveMethodTagsChips').children.length === 2, doc.getElementById('editSolveMethodTagsChips').children.length);

    // 2) 模拟用户输入新标签（回车）
    smInput.value = '数形结合';
    smInput.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter' }));
    check('输入新标签后 _getTags 含三项', smInput._getTags() === '配凑法,换元法,数形结合', smInput._getTags());
    check('输入新标签后 chip 数量=3', doc.getElementById('editSolveMethodTagsChips').children.length === 3, doc.getElementById('editSolveMethodTagsChips').children.length);

    // 3) 切换题目：selectQuestion 起点应同步 resetEditTagInputs —— 这是防串味的关键
    win.resetEditTagInputs();
    const smInput2 = doc.getElementById('editSolveMethodTagInput');
    check('切题后: _getTags 返回空串（不会把A标签误存到B）', smInput2._getTags() === '', JSON.stringify(smInput2._getTags()));
    check('切题后: chips 已清空', doc.getElementById('editSolveMethodTagsChips').children.length === 0, doc.getElementById('editSolveMethodTagsChips').children.length);

    // 4) 载入题型 B（无解题方法）→ 应仍是空，不残留 A 的标签
    win.setupEditTagInput('editSolveMethodTags', 'editSolveMethodTagsChips', 'editSolveMethodTagInput', '');
    check('载入B: _getTags 仍为空', doc.getElementById('editSolveMethodTagInput')._getTags() === '', doc.getElementById('editSolveMethodTagInput')._getTags());

    // 5) 重复载入不应累积监听器：连续载入三次后，输入一次只新增一个 chip
    win.setupEditTagInput('editSolveMethodTags', 'editSolveMethodTagsChips', 'editSolveMethodTagInput', '');
    win.setupEditTagInput('editSolveMethodTags', 'editSolveMethodTagsChips', 'editSolveMethodTagInput', '');
    const sm3 = doc.getElementById('editSolveMethodTagInput');
    sm3.value = '待定系数法';
    sm3.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter' }));
    check('重复载入后单次输入仅新增1个chip(无监听器累积)', doc.getElementById('editSolveMethodTagsChips').children.length === 1, doc.getElementById('editSolveMethodTagsChips').children.length);
    check('重复载入后 _getTags=「待定系数法」', sm3._getTags() === '待定系数法', sm3._getTags());

    // 6) 知识点侧同样适用（验证两条字段都从元素读取）
    win.setupEditTagInput('editKnowledgeTags', 'editKnowledgeTagsChips', 'editKnowledgeTagInput', '函数,导数');
    check('知识点侧: _getTags 返回「函数,导数」', doc.getElementById('editKnowledgeTagInput')._getTags() === '函数,导数', doc.getElementById('editKnowledgeTagInput')._getTags());
    win.resetEditTagInputs();
    check('知识点侧切题后: _getTags 返回空', doc.getElementById('editKnowledgeTagInput')._getTags() === '', doc.getElementById('editKnowledgeTagInput')._getTags());
  } catch (e) {
    console.error('TEST EXCEPTION:', e.stack || e.message);
    results.push({ name: 'exception', pass: false, extra: e.message });
  }
  const failed = results.filter(r => !r.pass);
  console.log('=== SUMMARY ===', 'passed:', results.length - failed.length, '/', results.length);
  process.exit(failed.length === 0 ? 0 : 1);
}, 300);
