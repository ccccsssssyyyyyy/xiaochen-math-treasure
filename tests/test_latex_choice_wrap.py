"""回归测试：选择题选项包裹（latex_normalize._wrap_inline_choices）的偏移对齐。

HIGH-1 修复前，选项块切分在「掩码后的 protected（数学被占位）」上计算偏移，
却用原始 content 做切片，二者因公式占位导致长度不一致，最终选项正文错位、
把题面公式吞进选项或选项错乱。本测试锁定「公式在选项之前」这一触发场景。
"""
import re

from mathbank.latex_normalize import normalize_choice_options_to_latex


def _choices_block(result):
    m = re.search(r"\\begin\{choices\}([\s\S]*?)\\end\{choices\}", result)
    return m.group(1) if m else ""


def test_math_before_choices_wraps_without_offset_corruption():
    content = (
        "设 $x = 1 + 2$ 成立，则 "
        "A. 正确 B. 错误 C. 不确定 D. 无法判断"
    )
    result = normalize_choice_options_to_latex(content)

    # 只应包裹出一个 choices 环境
    assert result.count("\\begin{choices}") == 1

    block = _choices_block(result)
    # 选项块内不得混入题面公式（修复前的错位会把 $x = 1 + 2$ 切进 block）
    assert "$x = 1 + 2$" not in block
    # 四个选项正文应被完整、正确提取
    assert "\\item 正确" in block
    assert "\\item 错误" in block
    assert "\\item 不确定" in block
    assert "\\item 无法判断" in block
    # 原公式应被完整还原，并保留在选项块之外
    assert "$x = 1 + 2$" in result


def test_long_formula_before_choices_is_preserved():
    # 更长的公式产生更大的偏移差，进一步压测对齐逻辑
    content = (
        "计算 $\\int_0^1 x^2\\,dx = \\frac{1}{3}$ 的结果： "
        "A. 1/3 B. 1/2 C. 2/3 D. 1/4"
    )
    result = normalize_choice_options_to_latex(content)

    assert result.count("\\begin{choices}") == 1
    block = _choices_block(result)
    assert "x^2" not in block
    assert "\\item 1/3" in block
    assert "\\item 1/4" in block
    assert "\\int_0^1 x^2\\,dx = \\frac{1}{3}" in result


def test_already_wrapped_is_idempotent():
    content = "\\begin{choices}\n\\item 正确\n\\item 错误\n\\end{choices}"
    result = normalize_choice_options_to_latex(content)
    assert result.count("\\begin{choices}") == 1
