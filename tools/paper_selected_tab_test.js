// 验证 paper.js 中「已选试题」tab 的下移按钮不再引用未定义的 displayList
const fs = require('fs');

const code = fs.readFileSync('/Users/ccsssy/WorkBuddy/2026-08-24-00-26-36/math-question-bank/static/js/paper.js', 'utf8');

// 1. 语法校验（node --check 已通过，这里二次确认）
new Function(code);

// 2. 关键：在 selected tab 分支里，index 比较必须用 paperVirtual.displayList
if (/index\s*===\s*displayList\.length\s*-\s*1/.test(code)) {
  throw new Error('FAIL: paper.js still contains undefined "displayList" reference in selected tab');
}

// 3. 确认修复后的正确引用存在
if (!/index\s*===\s*paperVirtual\.displayList\.length\s*-\s*1/.test(code)) {
  throw new Error('FAIL: expected paperVirtual.displayList.length - 1 reference not found');
}

console.log('PASS: selected tab uses paperVirtual.displayList and avoids ReferenceError');
