"""前端来源归一（editor.js normalizeSource）vm 回归 + 前后端 parity 测试。

normalizeSource 在 vm 中注入后端同款 CANONICAL_MAP（模拟初始化 fetch 的结果），
对一批输入分别用 JS 与 Python 计算，断言两者完全一致 —— 验证「前端预览 == 后端落库」。

纯函数（仅 String/regex/split/replace，不依赖 DOM），可直接在 vm 注入运行。
"""
import json
import os
import subprocess
import sys
import pytest

JS = "/Users/ccsssy/WorkBuddy/2026-08-24-00-26-36/math-question-bank/static/js/editor.js"


def _resolve_node():
    """定位可用的 node 可执行文件。

    托管 node 的版本目录可能带后缀（如 22.22.2-2），硬编码单一路径会随升级失效，
    改为按「PATH -> 托管版本目录（新版优先）-> 常见系统路径」依次探测。
    """
    import glob
    import shutil
    candidates = [shutil.which("node")]
    candidates += sorted(
        glob.glob("/Users/ccsssy/.workbuddy/binaries/node/versions/*/bin/node"),
        reverse=True,
    )
    candidates += ["/usr/local/bin/node", "/opt/homebrew/bin/node"]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    raise RuntimeError("未找到 node 可执行文件，无法运行前端 parity 测试")


NODE = _resolve_node()

from mathbank.source_normalize import normalize_source, CANONICAL_MAP


def _extract_function(source, name):
    marker = "function %s(" % name
    start = source.index(marker)
    brace_open = source.index("{", start)
    depth = 0
    i = brace_open
    while i < len(source):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1
    raise ValueError("未找到函数 %s 的匹配右括号" % name)


def _run_js(func_src, map_obj, inputs):
    script = (
        "var SOURCE_CANONICAL_MAP = " + json.dumps(map_obj, ensure_ascii=False) + ";\n"
        + func_src + "\n"
        "const inputs = " + json.dumps(inputs, ensure_ascii=False) + ";\n"
        "console.log(JSON.stringify(inputs.map(x => normalizeSource(x))));\n"
    )
    res = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, "node 执行失败: " + res.stderr
    return json.loads(res.stdout.strip())


@pytest.fixture(scope="module")
def func_src():
    with open(JS, "r", encoding="utf-8") as f:
        return _extract_function(f.read(), "normalizeSource")


def test_js_backend_parity(func_src):
    """同一批输入，前端 normalizeSource（注入后端映射）与后端 normalize_source 结果一致。"""
    # 原始写法（key != value）、规约终态、空值、若干全新输入
    raw_forms = [k for k, v in CANONICAL_MAP.items() if k != v]
    canonical_finals = list({v for v in CANONICAL_MAP.values()})
    # 周练裸写式（Tier 1.5 / 2.5 通用规则）：映射表内 + 仅靠通用规则覆盖的，
    # 均须前后端一致，否则会出现「前端预览显示原始串、落库却已归一」的错位。
    zhoulian = [
        "石室中学高2028届高一上第三周练",   # 映射表内有精确条目
        "石室中学高2028届高一上周练",       # 无周次，仅通用规则覆盖
        "石室中学高2028届高一上第四周练",
        "树德中学高2025级高一上周练",       # 级 vs 届 学年推导不同
        "成外高2029届高二下第3周练",        # 学校别名 + 非高一学段
        "石室中学高2028届高一上第三周练数学试题",  # 尾缀噪声（Tier 2.5）
        "石室中学 高2028届 高一上 第三周练",        # 多余空格
        "石室中学高2028届高一上10月月考",   # 非周练，不得被误伤
    ]
    extras = [
        "", None, "   ",
        "2026-2027学年四川省成都市七中万达高一（上）期末数学试题(1)",
    ] + zhoulian
    inputs = raw_forms + canonical_finals + extras

    js_out = _run_js(func_src, CANONICAL_MAP, inputs)
    py_out = [normalize_source(x) for x in inputs]

    mismatches = [(i, a, b) for i, a, b in zip(inputs, js_out, py_out) if a != b]
    assert not mismatches, "前后端不一致: %r" % mismatches


def test_js_tier2_fallback_when_map_absent(func_src):
    """映射表未加载（null）时退化为 Tier2 结构兜底，且不再抛错。"""
    inputs = ["树德中学2025-2026学年高一上学期1月期末测试数学试题(1)", "高一上成外10月月考2025-2026学年"]
    js_out = _run_js(func_src, None, inputs)
    # Tier2 仅去噪声 / 别名，不补全结构（与后端 Tier1 结果不同，但必须是合法非空字符串）
    assert js_out[0] == "树德中学2025-2026学年高一上学期1月期末测试"
    assert js_out[1] == "高一上成都外国语学校10月月考2025-2026学年"
