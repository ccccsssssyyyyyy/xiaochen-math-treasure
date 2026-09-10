"""防御性契约测试：选择题选项正文的残留标号必须被剥离，避免「A. A.」重复。

回归背景：
    Word 拆解产出的选择题常形如 `\\item A. $\\frac{8\\pi}{5}$`——选项正文里
    带着显式标号 A./B./C./D.。而预览渲染 choices 环境时会自动补 A./B./C./D.
    标签（editor.js 的 `\\begin{choices}` 处理分支），两者叠加就渲染成
    「A. A. 8π/5」这类重复标号。

    另一处连带缺陷：自动包裹 `$...$` 的逻辑没有排除「数学已被占位符保护」
    的情况，会把「A.」一起包进数学模式，进一步加重乱码。

本测试分两层：
    1. 源码契约（无需 node runtime）——锁定关键不变式与执行顺序；
    2. node 实跑（node 可用时）——在 vm 沙箱里真实执行 preprocessFormulaForKaTeX，
       校验渲染结果。node 不可用时跳过，不影响 CI。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDITOR_JS_PATH = PROJECT_ROOT / "static" / "js" / "editor.js"
NODE_CHECK_PATH = Path(__file__).resolve().parent / "js" / "choices_label_dedupe_check.js"


def _read_source() -> str:
    return EDITOR_JS_PATH.read_text(encoding="utf-8")


def _choices_block(source: str) -> str:
    """抠出 `\\begin{choices}` 处理分支（从注释起至该函数返回结束）。"""
    marker = "// Process choices environment"
    start = source.find(marker)
    if start < 0:
        return ""
    return source[start:start + 3200]


def test_choices_item_label_regex_declared() -> None:
    """必须声明 CHOICES_ITEM_LABEL_RE，覆盖 A. / （A） / A、 / A) 四种形态。"""
    src = _read_source()
    m = re.search(r"CHOICES_ITEM_LABEL_RE\s*=\s*/(.+?)/", src)
    assert m, "未找到 CHOICES_ITEM_LABEL_RE 声明"
    pattern = m.group(1)
    assert "[A-Ea-e]" in pattern, "标号正则需限定在 A-E 选项字母范围内"
    assert "（" in pattern and "）" in pattern, "需覆盖全角括号形态 （A）"
    assert "\\." in pattern and "、" in pattern, "需覆盖 A. 与 A、 形态"


def test_label_strip_happens_before_math_wrapping() -> None:
    """剥离残留标号必须发生在 `\\item ... $` 自动包裹之前。

    顺序错了会把「A.」一起包进数学模式，渲染成斜体字母而非纯文本标号。
    """
    block = _choices_block(_read_source())
    assert block, "未定位到 choices 处理分支"
    strip_pos = block.find("CHOICES_ITEM_LABEL_RE, '')")
    wrap_pos = block.find("Auto-wrap LaTeX math macros")
    assert strip_pos > 0, "choices 分支内未调用 CHOICES_ITEM_LABEL_RE 剥离"
    assert wrap_pos > 0, "choices 分支内未找到自动包裹数学模式逻辑"
    assert strip_pos < wrap_pos, "标号剥离必须早于数学包裹，否则 A. 会被包进 $...$"


def test_math_wrapping_guarded_by_protected_placeholder() -> None:
    """自动包裹需有占位符守卫：数学已被保护时不得重复包裹。"""
    block = _choices_block(_read_source())
    assert "hasProtectedMath" in block, "缺少 hasProtectedMath 守卫，已受保护的数学会被二次包裹"
    assert re.search(
        r"if\s*\(!hasProtectedMath\s*&&.*?test\(cleanItem\)", block, re.DOTALL
    ), "hasProtectedMath 必须作为自动包裹的前置条件参与短路"


def test_choices_label_dedupe_runtime() -> None:
    """node 可用时，在 vm 沙箱里真实执行渲染函数并校验输出。"""
    node_bin = shutil.which("node")
    if not node_bin:
        return  # node 不可用则跳过实跑，源码契约已覆盖关键不变式

    proc = subprocess.run(
        [node_bin, str(NODE_CHECK_PATH), str(EDITOR_JS_PATH)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "选项标号去重实跑未通过:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
