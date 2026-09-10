"""防御性契约测试：多文件拆解时同一完成回调不能被多次执行。

回归背景：
    用户批量上传 ~7 个 docx（每个 ~19 题）后，前端审查列表出现 48 个分组、
    1483 道题；根因是 handleParseTaskCompleted 完成回调完全无幂等位，
    任务 completed 后多个并发 fetch 都会到达同一回调，把同一份文件多次 append。
    配合 advanceQueueAfterParse 也会被多次推进，队列状态进一步错位。

本测试不依赖 jsdom runtime（避免引入大体积 node_modules），只做源码契约。
字符串/正则扫描足以锁定关键不变式。
"""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_JS_PATH = PROJECT_ROOT / "static" / "js" / "import.js"


def _read_source() -> str:
    return IMPORT_JS_PATH.read_text(encoding="utf-8")


def _slice_function_body(source: str, name: str, scope_lines: int = 60) -> str:
    """从源码里抠出指定函数的前 scope_lines 行（含函数体头部），找不到返回空串。

    简化策略：用正则定位 `function NAME` 或 `<NAME> = function` 的位置，
    从该位置往下取 scope_lines 行，足够覆盖守卫块头部。
    """
    pattern = re.compile(
        r"(?:function\s+" + re.escape(name) + r"\s*\(|"
        + re.escape(name) + r"\s*=\s*function\s*\()",
        re.MULTILINE,
    )
    m = pattern.search(source)
    if not m:
        return ""
    tail = source[m.start():]
    lines = tail.splitlines()[:scope_lines]
    return "\n".join(lines)


def test_completedTaskIds_global_set_declared_once() -> None:
    """completedTaskIds 必须在 IIFE 顶层恰好声明一次（const 完成）。"""
    src = _read_source()
    matches = list(
        re.finditer(r"\b(?:const|let)\s+completedTaskIds\s*=\s*new\s+Set\(\)", src)
    )
    assert len(matches) == 1, (
        "completedTaskIds 应在 IIFE 顶层恰好声明一次；"
        f"实际 {len(matches)} 次。重声明会导致进程内多个 Set 实例，" \
        "无法跨函数去重。"
    )


def test_handleParseTaskCompleted_dedupes_by_taskId() -> None:
    """handleParseTaskCompleted 头部必须按 taskId 去重，且命中时早返回。"""
    src = _read_source()
    body = _slice_function_body(src, "handleParseTaskCompleted", scope_lines=30)
    assert body, "必须存在 handleParseTaskCompleted 函数"
    # 必须按 taskId 去重
    assert re.search(r"completedTaskIds\.has\(\s*taskId\s*\)", body), (
        "handleParseTaskCompleted 应在头部检查 completedTaskIds.has(taskId)"
    )
    assert re.search(r"completedTaskIds\.add\(\s*taskId\s*\)", body), (
        "handleParseTaskCompleted 应在确认是新 taskId 后立即加入集合"
    )
    # 必须 has 后早 return
    # 注意：此处只锁定「命中即早返回」这一不变式，不要求守卫体内出现 console.log——
    # 调试日志在 e1fa008「清理调试 console.log / 死代码」中已被有意移除，
    # 旧正则硬编码 console.log 导致守卫本身合规却报错（测试脆弱，非代码缺陷）。
    has_return_after_has = re.search(
        r"completedTaskIds\.has\([^)]*\)\s*\)\s*\{[^{}]*?return\s*;",
        body,
        re.DOTALL,
    )
    assert has_return_after_has, (
        "handleParseTaskCompleted 头部应存在 "
        "`if (completedTaskIds.has(taskId)) { ...; return; }` 早返回守卫"
    )


def test_beginDocumentImportTask_clears_completedTaskIds() -> None:
    """beginDocumentImportTask 必须清空 completedTaskIds，避免历史 taskId 干扰。"""
    src = _read_source()
    body = _slice_function_body(src, "beginDocumentImportTask", scope_lines=20)
    assert body, "必须存在 beginDocumentImportTask 函数"
    assert "completedTaskIds.clear()" in body, (
        "beginDocumentImportTask 必须调用 completedTaskIds.clear()"
    )


def test_advanceQueueAfterParse_has_inflight_guard() -> None:
    """advanceQueueAfterParse 必须用 __queueAdvanceInFlight 做幂等。"""
    src = _read_source()
    body = _slice_function_body(src, "advanceQueueAfterParse", scope_lines=80)
    assert body, "必须存在 advanceQueueAfterParse = function(...) 赋值"
    # 必须设置入口守卫
    assert re.search(
        r"if\s*\(\s*window\.__queueAdvanceInFlight\s*\)\s*\{[^}]*return\s*;",
        body,
        re.DOTALL,
    ), "advanceQueueAfterParse 必须在入口检查 window.__queueAdvanceInFlight 并早 return"
    # 必须有 try/finally 包裹，防止异常下无法释放
    assert "window.__queueAdvanceInFlight = true" in body, (
        "advanceQueueAfterParse 必须把 __queueAdvanceInFlight 置 true"
    )
    assert body.count("__queueAdvanceInFlight") >= 3, (
        "advanceQueueAfterParse 至少出现 3 次 __queueAdvanceInFlight 引用："
        "入口守卫 + 置 true + finally 置 false"
    )
    # 校验 finally 分支存在
    assert re.search(r"\}\s*finally\s*\{[^}]*__queueAdvanceInFlight\s*=\s*false", body, re.DOTALL), (
        "advanceQueueAfterParse 必须用 try/finally 释放 __queueAdvanceInFlight"
    )


def _find_balanced_close(src: str, opener_idx: int) -> int:
    """从 opener_idx (指向 '(') 起平衡括号配对，返回匹配的 ')' 索引。跳过字符串/模板/注释。
    用于解析多行 setInterval/IIFE 等嵌套调用。"""
    depth = 0
    i = opener_idx
    in_str = False
    str_ch = None
    in_line_cmt = False
    in_block_cmt = False
    in_tpl = False
    while i < len(src):
        c = src[i]
        nxt = src[i + 1] if i + 1 < len(src) else ""
        if in_line_cmt:
            if c == "\n":
                in_line_cmt = False
            i += 1
            continue
        if in_block_cmt:
            if c == "*" and nxt == "/":
                in_block_cmt = False
                i += 2
                continue
            i += 1
            continue
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == str_ch:
                in_str = False
            i += 1
            continue
        if in_tpl:
            if c == "\\":
                i += 2
                continue
            if c == "`":
                in_tpl = False
            i += 1
            continue
        if c == "/" and nxt == "/":
            in_line_cmt = True
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_block_cmt = True
            i += 2
            continue
        if c in ("'", '"'):
            in_str = True
            str_ch = c
            i += 1
            continue
        if c == "`":
            in_tpl = True
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def test_pollPdfTaskStatus_setInterval_has_reasonable_delay() -> None:
    """pollPdfTaskStatus 内的 setInterval 必须传合理 delay（>=1000ms 量级）。"""
    src = _read_source()
    assert "pollPdfTaskStatus" in src, "必须存在 pollPdfTaskStatus 函数"
    delays: list[int] = []
    for m in re.finditer(r"setInterval\s*\(", src):
        close = _find_balanced_close(src, m.end() - 1)
        if close == -1:
            continue
        # 在 close 之前最近 ", 数字" 即为 delay
        before = src[:close]
        dm = re.search(r",\s*(\d+)\s*$", before.rstrip())
        if not dm:
            continue
        # 简单确认这是 setInterval 的尾巴（不是嵌套回调里某次巧合）
        # 取尾巴 ", NNN)" 距离 setInterval 起始 <= 15000 字符即认为合理
        if close - m.start() > 15000:
            continue
        delays.append(int(dm.group(1)))
    assert delays, "must have at least one setInterval(..., NNN)"
    assert all(d >= 1000 for d in delays), (
        "setInterval delay 至少为 1000ms 量级以避免并发 fetch 风暴；"
        f"实际 delays={delays}"
    )


def test_import_js_syntax_via_node_check() -> None:
    """端到端：node --check 静态验证 import.js 是合法 JS。"""
    import subprocess

    result = subprocess.run(
        ["node", "--check", str(IMPORT_JS_PATH)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, (
        f"node --check 失败：\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
