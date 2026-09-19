"""KaTeX 分隔符配置的收敛契约。

背景见 `static/js/math-render.js` 顶部注释：同一段 4 行 delimiters 配置曾在
editor / import / paper / mistake 四个文件里抄了 13 遍，而 paper.js 自己就
抄错过一次 —— 定义了 `PAPER_KATEX_DELIMS` 常量，500 行之外又手抄了一份。

抄错不会报错，只会让同一道题在两处排出不同的样子（`$$` 与 `\\[ \\]` 是行内
还是独占一行），肉眼还得两个页面来回切才看得出来。所以这里钉住三条：

1. 分隔符配置只有 `math-render.js` 一个来源，其它文件不许再出现字面量；
2. 不许绕过 `window.MathRender` 直接调 `renderMathInElement`；
3. 该模块必须登记进缓存刷新白名单，且早于任何使用方加载。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JS_DIR = PROJECT_ROOT / "static" / "js"
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"
MAIN_PY = PROJECT_ROOT / "main.py"

CANONICAL = "math-render.js"
# 唯一允许持有分隔符字面量与直接调用 KaTeX 的文件
ALLOWED = {CANONICAL}

# 使用方：任何一个先于 math-render.js 加载都会在运行期拿到 undefined
DEPENDENTS = ["api.js", "editor.js", "import.js", "paper.js", "mistake.js"]

BARE_DELIMITER = re.compile(r"left\s*:\s*['\"]\$\$['\"]")
BARE_CALLS = [
    re.compile(r"(?<![\w.])renderMathInElement\s*\("),  # 裸全局调用
    re.compile(r"window\.renderMathInElement\s*\("),     # 显式 window 调用
]


def _js_files():
    return sorted(p for p in JS_DIR.glob("*.js") if p.name not in ALLOWED)


@pytest.fixture(scope="module")
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def canonical():
    return (JS_DIR / CANONICAL).read_text(encoding="utf-8")


def test_canonical_module_exists(canonical):
    assert "window.MathRender" in canonical
    for key in ("DELIMS_INLINE", "DELIMS_DISPLAY", "render"):
        assert key in canonical, f"math-render.js 没有导出 {key}"
    assert canonical.count("left: '$$'") == 2, "应当只有两套语义（行内 / 行间）"


def test_no_bare_delimiter_config_outside_canonical():
    offenders = []
    for path in _js_files():
        if BARE_DELIMITER.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert offenders == [], (
        "这些文件又抄了一份 KaTeX 分隔符配置，请改走 window.MathRender.render(): "
        f"{offenders}"
    )


def test_no_direct_render_math_in_element_calls_outside_canonical():
    offenders = []
    for path in _js_files():
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in BARE_CALLS):
            offenders.append(path.name)
    assert offenders == [], (
        "这些文件绕过了统一入口直接调 renderMathInElement，异常兜底与配置会再次分叉: "
        f"{offenders}"
    )


def test_inline_and_display_modes_differ_only_in_display_flag(canonical):
    inline = re.search(r"DELIMS_INLINE = \[(.*?)\];", canonical, re.S).group(1)
    display = re.search(r"DELIMS_DISPLAY = \[(.*?)\];", canonical, re.S).group(1)

    # 行内：$$ 与 \[ \] 都不独占一行；行间：两者都独占一行
    assert re.search(r"left: '\$\$', right: '\$\$', display: false", inline)
    assert re.search(r"left: '\\\\\[', right: '\\\\\]', display: false", inline)
    assert re.search(r"left: '\$\$', right: '\$\$', display: true", display)
    assert re.search(r"left: '\\\\\[', right: '\\\\\]', display: true", display)
    # 单美元始终行内，两套一致
    assert inline.count("{ left: '$', right: '$', display: false }") == 1
    assert display.count("{ left: '$', right: '$', display: false }") == 1


def test_math_render_is_registered_for_cache_busting(html):
    assert f'src="/static/js/{CANONICAL}' in html, "index.html 没有引入 math-render.js"

    main_source = MAIN_PY.read_text(encoding="utf-8")
    block = re.search(r"js_files\s*=\s*\[(.*?)\]", main_source, re.S)
    assert block, "main.py 里找不到 js_files 列表"
    listed = set(re.findall(r'"([^"]+\.js)"', block.group(1)))
    assert CANONICAL in listed, (
        "math-render.js 不在 main.py 的 js_files 里 —— 它的 ?v= 会停在占位值，"
        "改了浏览器也不刷新"
    )


def test_math_render_loads_before_every_dependent(html):
    assert f"/static/js/{CANONICAL}" in html, "index.html 没有引入 math-render.js"
    canonical_at = html.index(f"/static/js/{CANONICAL}")
    for name in DEPENDENTS:
        if name not in html:
            continue
        assert canonical_at < html.index(f"/static/js/{name}"), (
            f"{name} 在 math-render.js 之前加载，window.MathRender 会是 undefined"
        )


def test_math_render_passes_node_syntax_check():
    node = shutil.which("node")
    if not node:
        pytest.skip("环境里没有 node，跳过语法检查")
    result = subprocess.run(
        [node, "--check", str(JS_DIR / CANONICAL)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
