// splitOcrTextByAnswerMarker 纯函数单测（从 static/js/ocr.js 提取源码后 vm 执行）。
// 用法：node tools/split_ocr_text_test.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'js', 'ocr.js'),
  'utf8'
);
// 匹配从函数定义到第一个 8 空格的闭合 }（该函数体内部块以 12 空格闭合，独此一份 8 空格 })
const m = src.match(
  /function splitOcrTextByAnswerMarker\(text\) \{[\s\S]*?\n        \}/
);
if (!m) {
  console.error('FAIL: splitOcrTextByAnswerMarker not found in ocr.js');
  process.exit(1);
}
const ctx = vm.createContext({});
vm.runInContext(
  m[0] + '\nthis.splitOcrTextByAnswerMarker = splitOcrTextByAnswerMarker;',
  ctx
);
const split = ctx.splitOcrTextByAnswerMarker;

let pass = 0,
  fail = 0;
function check(label, cond) {
  if (cond) {
    pass++;
    console.log('PASS:', label);
  } else {
    fail++;
    console.log('FAIL:', label);
  }
}

// 1. 无任何答案标记：整段保留为 question，answer 为空
let r = split('已知 x>0，求 f(x) 的解析式。\nA. $a$ B. $b$');
check('无标记 answer 为空', r.answer === '');
check('无标记 question 等于原文', r.question === '已知 x>0，求 f(x) 的解析式。\nA. $a$ B. $b$');

// 2. 仅含【答案】：从【答案】切
const t2 = '题干 stem\nA. $a$\nB. $b$\n【答案】A\n【分析】some analysis';
r = split(t2);
check('【答案】切分：question 不含【答案】', !r.question.includes('【答案】'));
check('【答案】切分：answer 以【答案】A 开头', r.answer.startsWith('【答案】A'));
check('【答案】切分：question 保留题干', r.question.includes('题干 stem'));
check('【答案】切分：answer 含【分析】', r.answer.includes('【分析】some analysis'));

// 3. 仅含【分析】（无【答案】）：按【分析】切
const t3 = '题干\nA. 1\nB. 2\n【分析】分';
r = split(t3);
check('【分析】切分：answer 以【分析】开头', r.answer.startsWith('【分析】分'));
check('【分析】切分：question 不含【分析】', !r.question.includes('【分析】'));

// 4. 仅含【详解】：按【详解】切
const t4 = '题干\n【详解】详解内容';
r = split(t4);
check('【详解】切分：answer 以【详解】开头', r.answer.startsWith('【详解】详解内容'));

// 5. 三者都有，取最早
const t5 = '题干\n【分析】AAA\n【答案】BBB\n【详解】CCC';
r = split(t5);
check('三标记取最早【分析】', r.answer.startsWith('【分析】AAA'));
check('三标记 question 仅题干', r.question === '题干');

// 6. 标记紧贴开头（question 段为空）
const t6 = '【答案】A\n【分析】x';
r = split(t6);
check('标记开头：question 为空', r.question === '');
check('标记开头：answer 完整保留', r.answer === '【答案】A\n【分析】x');

// 7. 切点两端空白被 trim
const t7 = '题干\n  \n【答案】A';
r = split(t7);
check('切点前空白去除', r.question === '题干');
check('切点后空白去除', r.answer === '【答案】A');

// 8. 空串 / 纯空白
r = split('');
check('空串：question 空', r.question === '');
check('空串：answer 空', r.answer === '');

// 9. 半角 [答案] 锚点
const t9 = '题干\nA. 1\nB. 2\n[答案] A\n[解析] x';
r = split(t9);
check('半角 [答案]：answer 以 [答案] 开头', r.answer.startsWith('[答案] A'));
check('半角 [答案]：answer 含 [解析]', r.answer.includes('[解析] x'));

// 10. 半角 [详解] 单独出现
const t10 = '题干\n[详解] 内容';
r = split(t10);
check('半角 [详解]：answer 以 [详解] 开头', r.answer.startsWith('[详解] 内容'));

// 11. 冒号弱锚点：题目+选项 + 后段"答案：xxx"
const t11 =
  '某独唱比赛...(2024 全国甲卷高考真题)...概率是（ ）\n' +
  'A. 1/6\nB. 1/4\nC. 1/3\nD. 1/2\n\n' +
  '答案：B\n解析：略。';
r = split(t11);
check('冒号弱锚点（行首）：answer 以"答案"开头',
  r.answer.startsWith('答案：B'));
check('冒号弱锚点：question 仅题干与选项', /D\. 1\/2$/.test(r.question));
check('冒号弱锚点：answer 含解析', r.answer.includes('解析：略。'));

// 12. 题目中偶然出现"答案"不误切（中段、不是行首/换行）
const t12 = '已知答案是 A，原因是 B。求 f(x)。\n选项在此。';
r = split(t12);
check(
  '题目中"答案"在中段且非行首：answer 空',
  r.answer === ''
);
check(
  '题目中"答案"在中段且非行首：question 保持原样',
  r.question === t12
);

// 13. 强锚点 + 弱锚点同时存在，强锚点优先
const t13 = '题干\n【答案】A\n答案：被忽略';
r = split(t13);
check('强锚点 vs 弱锚点：强【答案】胜出', r.answer.startsWith('【答案】A'));

// 14. 只有冒号弱锚点、且出现在 1/3 之前（中前段） → 不切
const t14 = '答案：这是一道题的开头\nA. $a$\nB. $b$';
r = split(t14);
check('中前段弱锚点不触发切分', r.answer === '');

// 15. 冒号弱锚点恰好在 1/3 之后 → 触发切分
const filler = '填充'.repeat(20); // 60 chars
const t15 = `题干前半段 ${filler}\nA. $a$\nB. $b$\n答案：A`;
r = split(t15);
check(
  '中后段弱锚点命中切分：answer 以"答案"开头',
  r.answer.startsWith('答案：A')
);
check(
  '中后段弱锚点命中切分：question 仅保留到"答"之前',
  r.question === '题干前半段 ' + filler + '\nA. $a$\nB. $b$'
);

// 16. 答案/解析之间有空格：切点两端空白去除
const t16 = '题干\nA. 1\n\n【解析】  \n详细解答';
r = split(t16);
check('答案段前空白去除', r.answer.startsWith('【解析】'));
check('【解析】切分：question 保留题干与选项', /A\. 1/.test(r.question));

// 17. 多个变体早出现 → 取最早
const t17 = '题干\n【分析】分析内容\n[详解]详细';
r = split(t17);
check('多种锚点取最早【分析】', r.answer.startsWith('【分析】分析内容'));

console.log(`\nSUMMARY: ${pass}/${pass + fail} passed`);
process.exit(fail === 0 ? 0 : 1);