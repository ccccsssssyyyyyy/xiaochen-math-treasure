// 回归测试：多文件拆解时后续文件在后端队列排队被误判超时（P0-2）。
//
// 背景：后端 TaskManager(max_workers=2, max_queue=4)，任务在队列里 status=pending、progress 恒为 0。
// 前端旧逻辑仅当 task.progress 变化才刷新 lastProgressAt，于是排队期间静默计时一直累计，
// 10 分钟后被判「拆解无进展」而失败 —— 表现为「同时传 3 个文件，第 3 个失败；单独传却能成」。
//
// 修复：任务处于 pending/queued 时豁免静默超时（仍受 30 分钟绝对上限约束）。
// 本测试用 __PARSE_IDLE_TIMEOUT_MS=300ms 快速触发，验证两个相反方向：
//   A 排队中（pending, progress 0）→ 不得被判失败；
//   B 真卡死（processing, progress 恒定）→ 仍必须被判失败（不能把保护改废）。

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = require('path').resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'static/index.html'), 'utf8');
const codes = ['api', 'editor', 'ocr', 'import', 'paper'].map(
  n => fs.readFileSync(path.join(ROOT, 'static/js', `${n}.js`), 'utf8')
);

function buildDom() {
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://127.0.0.1:8000/', pretendToBeVisual: true });
  const win = dom.window; const doc = win.document;
  win.renderMathInElement = () => {};
  win.marked = { parse: x => x || '' };
  win.DOMPurify = { sanitize: x => x };
  win.tailwind = { config: {} };
  win.IntersectionObserver = class { observe(){} unobserve(){} disconnect(){} };
  win.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} };
  try { win.localStorage.setItem('local_token', 'test-token'); } catch (e) {}
  win.local_token = 'test-token';
  win.systemPreferParseModel = 'deepseek-chat';
  win.systemMetadata = {
    question_types: [
      { value: 'single_choice', label: '单选题' }, { value: 'multi_choice', label: '多选题' },
      { value: 'fill_in_blank', label: '填空题' }, { value: 'detailed_answer', label: '解答题' }
    ],
    difficulties: [{ value: 'easy', label: '易' }, { value: 'medium', label: '中' }, { value: 'hard', label: '难' }]
  };
  win.MathBankModal = { open(){}, close(){} };
  // 静默超时须【大于】轮询间隔(1500ms)，否则第一个 tick 必然超时、与任务状态无关，
  // 两个场景会得出相同结果而失去区分度。取 2500ms：
  //   tick1@1500ms 首次取回状态并置位 backendQueued；tick3@4500ms 时 idle=3000>2500 才判死。
  // 绝对上限放大到 10 分钟，避免干扰本用例。
  win.__PARSE_IDLE_TIMEOUT_MS = 2500;
  win.__PARSE_TIMEOUT_MS = 600000;
  return { win, doc };
}

// mode: 'queued' = 后端排队中；'stuck' = 处理中但进度不动
function runScenario(mode, waitMs = 7000) {
  return new Promise(resolve => {
    const { win, doc } = buildDom();
    win.fetch = async (url, opts) => {
      const u = String(url);
      if (u.includes('/api/documents/imported')) return { ok: true, json: async () => ({ imported: false, count: 0 }) };
      if (u.includes('/api/upload/pdf-task')) return { ok: true, json: async () => ({ status: 'success', task_id: 'T1' }) };
      if (u.includes('/api/tasks/') && u.includes('/status')) {
        const payload = mode === 'queued'
          ? { status: 'pending', progress: 0, page_images: [] }
          : { status: 'processing', progress: 50, page_images: [] };
        return { ok: true, json: async () => payload };
      }
      return { ok: true, json: async () => ({ status: 'success' }) };
    };
    for (const code of codes) {
      try { doc.body.appendChild(Object.assign(doc.createElement('script'), { textContent: code })); } catch (e) {}
    }

    setTimeout(() => {
      const input = doc.getElementById('texFileInput');
      const file = new win.File(['%PDF-1.4 dummy'], 'fileA.pdf', { type: 'application/pdf' });
      Object.defineProperty(input, 'files', { value: [file], configurable: true });
      input.dispatchEvent(new win.Event('change'));

      setTimeout(() => {
        try { win.startImportParseClick(); } catch (e) {}
        setTimeout(() => {
          const snap = win.__pendingFilesSnapshot || [];
          const subEl = doc.getElementById('importSubLoadingText');
          resolve({
            status: snap.length ? snap[0].status : '(无)',
            error: snap.length ? (snap[0].error || '') : '',
            subText: subEl ? (subEl.textContent || '') : ''
          });
          win.close();
        }, waitMs);
      }, 300);
    }, 200);
  });
}

const assert = (cond, label) => { console.log((cond ? '✅' : '❌') + ' ' + label); if (!cond) process.exitCode = 1; return cond; };

(async () => {
  console.log('=== 场景 A：任务在后端队列排队（pending, progress 恒为 0）===');
  const a = await runScenario('queued');
  console.log('   文件状态:', a.status, '| 错误:', a.error || '(无)');
  console.log('   等待提示:', a.subText || '(无)');
  const okA = assert(a.status !== 'failed', 'A1 排队中不因静默超时被误判 failed')
    && assert(/后端队列等待/.test(a.subText), 'A2 排队期间给出可见的等待提示');

  console.log('\n=== 场景 B：任务处理中但进度不变（processing, progress 恒定 50）===');
  const b = await runScenario('stuck');
  console.log('   文件状态:', b.status, '| 错误:', b.error || '(无)');
  const okB = assert(b.status === 'failed', 'B1 真正卡死时仍被判 failed（超时保护未被改废）')
    && assert(/无进展/.test(b.error), 'B2 失败原因标注为「拆解无进展」');

  console.log('\n=== 排队豁免回归 ===', (okA && okB) ? 'PASS' : 'FAIL');
  process.exit((okA && okB) ? 0 : 1);
})();
