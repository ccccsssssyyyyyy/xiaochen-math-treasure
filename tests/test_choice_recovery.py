"""Tests for the deterministic choice-option recovery fallback.

小参数模型拆题时会保留题干却丢弃 A/B/C/D 选项。``choice_recovery`` 用原文
做确定性回溯补回。这里的用例同时覆盖「能补就补」与「宁可跳过也不误补」。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathbank.choice_recovery import (  # noqa: E402
    extract_options,
    recover_missing_choices,
    split_question_blocks,
)


# 模拟 PDF 原生文本抽取结果：题号后无空格、数学符号带误码
SOURCE = """<!-- MATHBANK_PDF_PAGE:1 -->
某中学高一阶段性测试数学试卷
一、单选题
1.复数==i2026(1+i)(i是虚数单位)在复平面内对应的点位于第( )象限
A.一
B.二
C.三
D.四
2.水平放置的三角形ABC的直观图如图,那么原三角形ABC是一个( )
A.等边三角形
B.腰和底边不相等的等腰三角形
C.直角三角形
D.钝角三角形
3.已知a,b满足(a+b)·a=0,则投影向量为( )
A.2
B.-a
C.a
D.0
4.计算定积分的值
"""


def test_split_question_blocks_handles_tight_numbering():
    """PDF 原生文本题号后无空格（1.复数），也必须能切开。"""
    blocks = split_question_blocks(SOURCE)
    # 页眉行与「4.计算定积分的值」共 4 个题号边界
    assert len(blocks) == 4
    assert blocks[0][2].startswith("1.复数")
    assert blocks[1][2].startswith("2.水平放置")


def test_split_question_blocks_ignores_decimals():
    """3.14、1.5万 这类小数不能被当成题号。"""
    blocks = split_question_blocks("圆周率约等于3.14这个值\n1.5万人参加了考试")
    assert blocks == []


def test_split_question_blocks_empty_source():
    assert split_question_blocks("") == []
    assert split_question_blocks(None) == []


def test_extract_options_returns_consecutive_abcd():
    options = extract_options("1.题干内容\nA.一\nB.二\nC.三\nD.四\n")
    assert options == [("A", "一"), ("B", "二"), ("C", "三"), ("D", "四")]


def test_extract_options_ignores_orphan_letters():
    """题干中孤立的 A./C. 不构成选项段，必须返回空。"""
    assert extract_options("1.已知 A. 与 B. 两点\n求距离") == []


def test_extract_options_requires_min_options():
    block = "1.题干\nA.只有一项\n"
    assert extract_options(block, min_options=2) == []
    assert extract_options(block, min_options=1) == [("A", "只有一项")]


def test_recover_appends_choices_environment():
    questions = [
        {
            "question_type": "single_choice",
            "content": "复数 $z=i^{2026}(1+i)$（$i$ 是虚数单位）在复平面内对应的点位于第（ ）象限",
        }
    ]
    recovered = recover_missing_choices(questions, SOURCE)
    assert recovered == 1
    content = questions[0]["content"]
    assert "\\begin{choices}" in content
    assert "\\item 一" in content
    assert "\\item 四" in content
    # 题干本体必须原样保留
    assert "复数 $z=i^{2026}(1+i)$" in content


def test_recover_skips_when_choices_already_present():
    """模型已正确输出选项时，绝不能覆盖。"""
    questions = [
        {
            "question_type": "single_choice",
            "content": "题干（ ）\n\\begin{choices}\n\\item 甲\n\\item 乙\n\\end{choices}",
        }
    ]
    assert recover_missing_choices(questions, SOURCE) == 0
    assert "甲" in questions[0]["content"]


def test_recover_skips_non_choice_types():
    questions = [
        {"question_type": "detailed_answer", "content": "复数在复平面内对应的点位于第（ ）象限"},
        {"question_type": "fill_in_blank", "content": "复数在复平面内对应的点位于第（ ）象限"},
    ]
    assert recover_missing_choices(questions, SOURCE) == 0
    assert "\\begin{choices}" not in questions[0]["content"]
    assert "\\begin{choices}" not in questions[1]["content"]


def test_recover_skips_when_match_too_weak():
    """题干与原文毫无重合时不得乱补。"""
    questions = [{"question_type": "single_choice", "content": "完全不相干的题干内容xyz"}]
    assert recover_missing_choices(questions, SOURCE) == 0
    assert "\\begin{choices}" not in questions[0]["content"]


def test_recover_skips_when_source_has_no_question_blocks():
    questions = [{"question_type": "single_choice", "content": "复数在复平面内对应的点位于第（ ）象限"}]
    assert recover_missing_choices(questions, "一段没有任何题号的纯文本说明") == 0


def test_recover_does_not_assign_same_block_twice():
    """两道题不得抢同一题块导致选项互串。"""
    questions = [
        {"question_type": "single_choice", "content": "复数在复平面内对应的点位于第（ ）象限"},
        # 与上一题几乎同义，定位会落到同一块附近
        {"question_type": "single_choice", "content": "复数在复平面内对应的点位于第（ ）象限"},
        {"question_type": "single_choice", "content": "三角形ABC是一个（ ）"},
    ]
    recovered = recover_missing_choices(questions, SOURCE)
    assert recovered == 2
    # 第二题被跳过（同一块不可重复消费），第三题拿到自己的选项
    assert "\\begin{choices}" not in questions[1]["content"]
    assert "\\item 等边三角形" in questions[2]["content"]


def test_recover_handles_empty_inputs():
    assert recover_missing_choices([], SOURCE) == 0
    assert recover_missing_choices([{"question_type": "single_choice", "content": "x"}], "") == 0
    assert recover_missing_choices(None, SOURCE) == 0


def test_recover_real_pdf_extraction_fixture():
    """用真实 PDF 原生抽取文本 + 模型真实输出做端到端验证。

    模型改写过的题干（加了 $/\\sqrt 等）仍要能定位回原文。
    """
    source = (
        "一、单选题\n"
        "1.复数==i2026(1+i)(i是虚数单位)在复平面内对应的点位于第( )象限\nA.一\nB.二\nC.三\nD.四\n"
        "2.一艘渔船航行到A处时看灯塔B在A的南偏东15°,距离为12√6海里\nA.8√3\nB. 12√3\nC. 12√3+12\nD. 12\n"
    )
    questions = [
        {
            "question_type": "single_choice",
            "content": "复数 $z=i^{2026}(1+i)$（$i$ 是虚数单位）在复平面内对应的点位于第（ ）象限",
        },
        {
            "question_type": "single_choice",
            "content": "一艘渔船航行到 $A$ 处时看灯塔 $B$ 在 $A$ 的南偏东 $15^\\circ$，距离为 $12\\sqrt{6}$ 海里",
        },
    ]
    assert recover_missing_choices(questions, source) == 2
    assert "\\item 一" in questions[0]["content"]
    assert "\\item 8√3" in questions[1]["content"]


def test_recover_preserves_formula_lock_placeholders():
    """DOCX 链路原文含 [[Mn]] 公式占位符时，补回的选项要原样携带占位符。

    占位符需保留到后续 restore_visible_math 阶段统一还原，故不得在此展开。
    """
    source = (
        "1.已知函数fx在区间上的最大值与最小值的和为( )\n"
        "A. [[M1]]\nB. [[M2]]\nC. [[M3]]\nD. [[M4]]\n"
    )
    questions = [
        {
            "question_type": "multi_choice",
            "content": "已知函数 $f(x)$ 在区间上的最大值与最小值的和为（ ）",
        }
    ]
    assert recover_missing_choices(questions, source) == 1
    content = questions[0]["content"]
    for marker in ("[[M1]]", "[[M2]]", "[[M3]]", "[[M4]]"):
        assert marker in content


def test_recover_skips_when_stem_too_short_to_locate():
    """题干实义字符不足以唯一定位时，宁可跳过也不猜。"""
    source = "1.已知函数值\nA.甲\nB.乙\nC.丙\nD.丁\n"
    questions = [{"question_type": "single_choice", "content": "函数值（ ）"}]
    assert recover_missing_choices(questions, source) == 0
    assert "\\begin{choices}" not in questions[0]["content"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
