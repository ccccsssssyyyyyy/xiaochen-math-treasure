// 回归测试：保存成功后再次点「录入单题」不应误弹「未保存修改」弹窗
// 根因：saveQuestion 构造的 requestBackupSnapshot 缺 related_curriculums，
//       backupEditorState(requestSnapshot) 走 requestSnapshot 分支 → originalQuestionState.related_curriculums 为 undefined
//       editorMatchesBackupSnapshot 用 currentRelated('[]'或'[...]') === undefined 永远 false → isEditorModified 永久 true
//       于是 checkAndSwitch 把"刚保存完、未改动"的状态也判为脏，必弹窗。
// 修复：方案A 给 requestBackupSnapshot 补 related_curriculums；方案B 让 backupEditorState 在 requestSnapshot 分支 merge 默认字段。
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('/Users/ccsssy/.workbuddy/binaries/node/workspace/node_modules/jsdom');

const ROOT = '/Users/ccsssy/WorkBuddy/2026-08-24-00-26-36/math-question-bank';
const html = fs.readFileSync(path.join(ROOT, 'static/index.html'), 'utf8');
const fileNames = ['api', 'editor', 'ocr', 'import', 'paper'];
const codes = fileNames.map(n => fs.readFileSync(path.join(ROOT, 'static/js', `${n}.js`), 'utf8'));

// 移除 tailwind 内联配置脚本（依赖外部 tailwind.min.js，jsdom 默认不加载外部资源 → tailwind 未定义会报 ReferenceError）
const htmlSafe = html
    .replace(/<script src="\/static\/lib\/tailwind[^"]*"><\/script>/g, '')
    .replace(/<script>\s*\/\/ Prevent theme[\s\S]*?<\/script>/g, '');
const dom = new JSDOM(htmlSafe, { runScripts: 'dangerously', url: 'http://127.0.0.1:8000/', pretendToBeVisual: true });
const win = dom.window; const doc = win.document;
win.renderMathInElement = () => {}; win.marked = { parse: x => x || '' }; win.DOMPurify = { sanitize: x => x };
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

function check(name, cond) {
    console.log((cond ? 'PASS' : 'FAIL') + ' - ' + name);
    if (!cond) throw new Error('测试失败: ' + name);
}

// 准备：编辑区各字段填上与"保存请求快照"一致的内容，并设关联章节（非空，正是原 bug 触发条件）
const sample = {
    content: '已知 $f(x)=x^2$，求 $f(1)$。',
    answer_markdown: '【答案】$1$\n【分析】代入即可',
    review: '',
    question_type: 'single_choice',
    difficulty: 'easy_error',
    source: '2024·全国',
    category_compulsory: '高中',
    category_chapter: '函数',
    category_knowledge: '二次函数',
    image_paths: '[]',
    tags: ''
};
// select 元素的 .value 赋值必须匹配已有 <option>；jsdom 中分类是动态加载的（无 option），
// 手动注入与快照一致的 option，确保赋值生效，才能真实复现保存后的 DOM 状态。
function ensureOption(selId, val) {
    const sel = doc.getElementById(selId);
    if (!sel) return;
    const o = doc.createElement('option');
    o.value = val; o.textContent = val;
    sel.appendChild(o);
}
ensureOption('editQType', sample.question_type);
ensureOption('editDifficulty', sample.difficulty);
ensureOption('editCompulsory', sample.category_compulsory);
ensureOption('editChapter', sample.category_chapter);
ensureOption('editKnowledge', sample.category_knowledge);

doc.getElementById('editContent').value = sample.content;
doc.getElementById('editAnswerMarkdown').value = sample.answer_markdown;
doc.getElementById('editReview').value = sample.review;
doc.getElementById('editQType').value = sample.question_type;
doc.getElementById('editDifficulty').value = sample.difficulty;
doc.getElementById('editSource').value = sample.source;
doc.getElementById('editCompulsory').value = sample.category_compulsory;
doc.getElementById('editChapter').value = sample.category_chapter;
doc.getElementById('editKnowledge').value = sample.category_knowledge;
win.relatedChapters = [{ id: 10, name: '函数与方程' }]; // 非空数组 → 原 bug 必现

console.log('DBG relatedChapters=', JSON.stringify(win.relatedChapters));
console.log('DBG editContent=', JSON.stringify(doc.getElementById('editContent').value));
console.log('DBG editQType=', JSON.stringify(doc.getElementById('editQType').value));
console.log('DBG editDifficulty=', JSON.stringify(doc.getElementById('editDifficulty').value));
console.log('DBG editCompulsory=', JSON.stringify(doc.getElementById('editCompulsory').value));
console.log('DBG editChapter=', JSON.stringify(doc.getElementById('editChapter').value));
console.log('DBG editKnowledge=', JSON.stringify(doc.getElementById('editKnowledge').value));
console.log('DBG editSource=', JSON.stringify(doc.getElementById('editSource').value));
console.log('DBG editAnswerMarkdown=', JSON.stringify(doc.getElementById('editAnswerMarkdown').value));
console.log('DBG editReview=', JSON.stringify(doc.getElementById('editReview').value));
console.log('DBG editTags=', doc.getElementById('editTags') ? JSON.stringify(doc.getElementById('editTags').value) : 'NO_EL');

// 用例1：requestSnapshot 不含 related_curriculums（修复前的构造形态，由方案B兜底）
win.backupEditorState(123, null, { ...sample });
check('保存后(无related字段) isEditorModified 应为 false（方案B兜底）', win.isEditorModified() === false);

// 用例2：requestSnapshot 含 related_curriculums（方案A 修复后的构造形态）
win.backupEditorState(123, null, { ...sample, related_curriculums: JSON.stringify(win.relatedChapters) });
check('保存后(含related字段) isEditorModified 应为 false（方案A）', win.isEditorModified() === false);

// 反例：保存后再改题干 → 必须识别为已修改（否则方案B过度宽松）
doc.getElementById('editContent').value = sample.content + '（已修改）';
check('保存后再编辑题干 isEditorModified 应为 true', win.isEditorModified() === true);

console.log('ALL GREEN - 保存后误弹窗 bug 已修复');
process.exit(0);
