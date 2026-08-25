// 回归测试：拆卷审查目录竖栏标签格式
// 验证：多文件时标签为「（中文文件序号）题型缩写+文件内连续序号」，单文件无括弧。
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

for (const code of codes) { try { doc.body.appendChild(Object.assign(doc.createElement('script'), { textContent: code })); } catch (e) { console.error('[inject]', e.message); } }

function tocLabels() { return Array.from(doc.querySelectorAll('#parsedTOC .parsed-toc-item')).map(el => el.textContent); }
const results = [];
function check(name, cond, extra) { results.push({ name, pass: !!cond, extra }); console.log((cond?'PASS':'FAIL')+': '+name+(extra!==undefined?(' -> '+JSON.stringify(extra)):'')); }

setTimeout(() => {
  try {
    // 多文件：A(选1/填2/解3) + B(选1/选2/填3)
    win.eval(`
      parsedQuestionsData.length = 0;
      parsedFileGroups.length = 0;
      parsedFileGroups.push({ name:'A.pdf', startIndex:0, count:3 });
      parsedFileGroups.push({ name:'B.pdf', startIndex:3, count:3 });
      const mk = (t) => ({ question_type:t, difficulty:'normal', saved:false, source_file:'x', content:'x', answer_markdown:'y' });
      parsedQuestionsData.push(mk('single_choice'), mk('fill_in_blank'), mk('detailed_answer'),
                               mk('single_choice'), mk('single_choice'), mk('fill_in_blank'));
    `);
    win.__parsedReviewFilter = 'all';
    win.renderParsedQuestionsList(win.eval('parsedQuestionsData'));

    const labels = tocLabels();
    check('多文件共 6 个目录项', labels.length === 6, labels);
    check('文件A: （一）选1', labels[0] === '（一）选1', labels[0]);
    check('文件A: （一）填2', labels[1] === '（一）填2', labels[1]);
    check('文件A: （一）解3', labels[2] === '（一）解3', labels[2]);
    check('文件B: （二）选1', labels[3] === '（二）选1', labels[3]);
    check('文件B: （二）选2', labels[4] === '（二）选2', labels[4]);
    check('文件B: （二）填3', labels[5] === '（二）填3', labels[5]);

    // 单文件：无括弧
    win.eval(`
      parsedQuestionsData.length = 0;
      parsedFileGroups.length = 0;
      parsedFileGroups.push({ name:'one.pdf', startIndex:0, count:3 });
      const mk = (t) => ({ question_type:t, difficulty:'normal', saved:false, source_file:'one.pdf', content:'x', answer_markdown:'y' });
      parsedQuestionsData.push(mk('single_choice'), mk('fill_in_blank'), mk('detailed_answer'));
    `);
    win.renderParsedQuestionsList(win.eval('parsedQuestionsData'));
    const single = tocLabels();
    check('单文件共 3 个目录项', single.length === 3, single);
    check('单文件无括弧: 选1', single[0] === '选1', single[0]);
    check('单文件无括弧: 填2', single[1] === '填2', single[1]);
    check('单文件无括弧: 解3', single[2] === '解3', single[2]);
  } catch (e) {
    console.error('TEST EXCEPTION:', e.stack || e.message);
    results.push({ name: 'exception', pass: false, extra: e.message });
  }
  const failed = results.filter(r => !r.pass);
  console.log('=== SUMMARY ===', 'passed:', results.length - failed.length, '/', results.length);
  process.exit(failed.length === 0 ? 0 : 1);
}, 300);
