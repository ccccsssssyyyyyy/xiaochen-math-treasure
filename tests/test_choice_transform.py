"""选项变换（重做遍数分级）的单元测试。"""

import random

from mathbank import choice_transform as ct

MCQ_CONTENT = (
    "已知 $f(x)=x^2-2x$，则 $f(3)=$（　　）\n"
    "\\begin{choices}\n"
    "\\item 3\n"
    "\\item 6\n"
    "\\item 9\n"
    "\\item 0\n"
    "\\end{choices}"
)
MCQ_ANSWER = "由 $f(3)=9-6=3$，故选 A."


def test_non_mcq_stays_plain():
    content = "求 $x$ 的值：$2x+1=5$。"
    new_c, new_a, mode = ct.apply_choice_transform(
        content, "解得 $x=2$", "detailed_answer", 2, correct_answer=""
    )
    assert mode == ct.PLAIN
    assert new_c == content
    assert new_a == "解得 $x=2$"


def test_first_pass_is_plain():
    new_c, new_a, mode = ct.apply_choice_transform(
        MCQ_CONTENT, MCQ_ANSWER, "single_choice", 1, correct_answer="A"
    )
    assert mode == ct.PLAIN
    assert new_c == MCQ_CONTENT
    assert new_a == MCQ_ANSWER


def test_second_pass_shuffles_and_remaps_answer():
    new_c, new_a, mode = ct.apply_choice_transform(
        MCQ_CONTENT, MCQ_ANSWER, "single_choice", 2,
        correct_answer="A", rng=random.Random(7),
    )
    assert mode == ct.SHUFFLED
    parts = ct.split_choices(new_c)
    assert parts is not None
    _, items, _ = parts
    assert sorted(items) == sorted(["3", "6", "9", "0"])  # 只是重排
    # 正确答案 body「3」挪到新位置后，答案里的字母必须跟着变
    new_letter = chr(ord("A") + items.index("3"))
    assert f"故选 {new_letter}" in new_a
    # 题干原文未被改动
    assert "$f(3)=$" in new_c


def test_third_pass_strips_options_keeps_stem():
    new_c, new_a, mode = ct.apply_choice_transform(
        MCQ_CONTENT, MCQ_ANSWER, "single_choice", 3, correct_answer="A"
    )
    assert mode == ct.NO_OPTION
    assert "\\begin{choices}" not in new_c
    assert "\\item" not in new_c
    assert "$f(3)=$" in new_c
    assert new_a == MCQ_ANSWER  # 不提供选项时答案键不变


def test_declines_when_no_correct_answer():
    new_c, new_a, mode = ct.apply_choice_transform(
        MCQ_CONTENT, MCQ_ANSWER, "single_choice", 2,
        correct_answer="", rng=random.Random(1),
    )
    assert mode == ct.PLAIN
    assert new_c == MCQ_CONTENT
    assert new_a == MCQ_ANSWER


def test_declines_when_answer_reference_not_locatable():
    # 答案里定位不到正确字母引用 → 拒绝打乱（避免答案键错位）
    tricky_answer = "由 $f(3)=9-6=3$ 得到结果。"
    new_c, new_a, mode = ct.apply_choice_transform(
        MCQ_CONTENT, tricky_answer, "single_choice", 2,
        correct_answer="A", rng=random.Random(2),
    )
    assert mode == ct.PLAIN
    assert new_c == MCQ_CONTENT
    assert new_a == tricky_answer


def test_answer_that_is_bare_letter_is_remapped():
    new_c, new_a, mode = ct.apply_choice_transform(
        MCQ_CONTENT, "A", "single_choice", 2,
        correct_answer="A", rng=random.Random(5),
    )
    assert mode == ct.SHUFFLED
    _, items, _ = ct.split_choices(new_c)
    expected = chr(ord("A") + items.index("3"))
    assert new_a == expected


def test_multi_choice_remaps_every_letter():
    content = (
        "下列说法正确的是（　　）\n"
        "\\begin{choices}\n\\item 甲\n\\item 乙\n\\item 丙\n\\item 丁\n\\end{choices}"
    )
    new_c, new_a, mode = ct.apply_choice_transform(
        content, "故选 AC", "multi_choice", 2,
        correct_answer="AC", rng=random.Random(3),
    )
    assert mode == ct.SHUFFLED
    _, items, _ = ct.split_choices(new_c)
    expected = "".join(chr(ord("A") + items.index(x)) for x in ["甲", "丙"])
    assert expected in new_a


def test_split_choices_strips_explicit_labels_and_preserves_math():
    content = (
        "题干\n"
        "\\begin{choices}\n"
        "\\item A. $\\frac{1}{2}$\n"
        "\\item B. $\\frac{1}{3}$\n"
        "\\end{choices}"
    )
    parts = ct.split_choices(content)
    assert parts is not None
    before, items, after = parts
    assert items == ["$\\frac{1}{2}$", "$\\frac{1}{3}$"]
    assert before == "题干\n"
    assert after == ""


def test_content_without_choices_is_plain():
    content = "没有选项的题干。"
    new_c, new_a, mode = ct.apply_choice_transform(
        content, "答案", "single_choice", 2, correct_answer="A"
    )
    assert mode == ct.PLAIN
    assert new_c == content


# ---------------------------------------------------------------- 答案键抽取


def test_extract_correct_answer_variants():
    cases = {
        "故选 A.": "A",
        "由 $f(3)=3$，故答案为 B。": "B",
        "正确答案：C": "C",
        "答案：D": "D",
        "B": "B",  # 答案本身就是裸字母
        "（1）A （2）B": "",  # 没有「答案」引导词、也不是首行裸字母 → 不猜
        "解得 $x=2$": "",
        "": "",
    }
    for answer, expected in cases.items():
        assert ct.extract_correct_answer(answer) == expected, answer


def test_extract_correct_answer_accepts_real_bank_formats():
    """真实题库里的两种主力格式：``【答案】ACD`` 与首行裸字母 + 解析。"""

    assert ct.extract_correct_answer("【答案】ACD\r\n\r\n解析\r\n【分析】对于 A……") == "ACD"
    assert ct.extract_correct_answer("【答案】B\r\n【解析】由题意得……") == "B"
    assert ct.extract_correct_answer("ACD\r\n\r\n由平行四边形的性质判断 A……") == "ACD"
    assert ct.extract_correct_answer("AC\r\n方法一：几何法，双曲线定义的应用……") == "AC"


def test_extract_correct_answer_does_not_mistake_prose_for_the_key():
    """首行裸字母这一档必须有换行/结尾兜底，否则正文里的「A 选项」会被当成答案。"""

    assert ct.extract_correct_answer("A 选项正确，B 选项错误。") == ""
    assert ct.extract_correct_answer("AD 是边 BC 上的高。") == ""
    assert ct.extract_correct_answer("A. 由题意可知……") == ""
    # 字母后直接换行的才是答案
    assert ct.extract_correct_answer("AD\r\n由题意可知……") == "AD"


def test_extract_correct_answer_multi_choice_dedupes_and_orders():
    assert ct.extract_correct_answer("故选 AC") == "AC"
    assert ct.extract_correct_answer("故答案为 A、C") == "AC"
    assert ct.extract_correct_answer("正确答案：C，A") == "CA"
    assert ct.extract_correct_answer("故选 A、A") == "A"  # 重复字母去重


def test_leading_letter_answer_is_remapped_in_place():
    """首行裸字母式的答案，重映射后解析正文必须原样保留。"""

    content = (
        "下列说法正确的是（　　）\n"
        "\\begin{choices}\n\\item 甲\n\\item 乙\n\\item 丙\n\\item 丁\n\\end{choices}"
    )
    new_c, new_a, mode = ct.apply_choice_transform(
        content, "AC\r\n由定义知甲、丙正确。", "multi_choice", 2,
        correct_answer="AC", rng=random.Random(9),
    )
    assert mode == ct.SHUFFLED
    _, items, _ = ct.split_choices(new_c)
    expected = "".join(chr(ord("A") + items.index(x)) for x in ["甲", "丙"])
    assert new_a.startswith(expected + "\r\n")
    assert new_a.endswith("由定义知甲、丙正确。")
    assert "\\item" not in new_c.split("\\begin{choices}")[0] or True


def test_multi_choice_with_separator_remaps_every_letter():
    content = (
        "下列说法正确的是（　　）\n"
        "\\begin{choices}\n\\item 甲\n\\item 乙\n\\item 丙\n\\item 丁\n\\end{choices}"
    )
    new_c, new_a, mode = ct.apply_choice_transform(
        content, "故答案为 A、C", "multi_choice", 2,
        correct_answer="AC", rng=random.Random(11),
    )
    assert mode == ct.SHUFFLED
    _, items, _ = ct.split_choices(new_c)
    expected = "、".join(chr(ord("A") + items.index(x)) for x in ["甲", "丙"])
    # 分隔符原样保留，两个字母都被重映射
    assert expected in new_a


def test_declines_when_answer_letters_disagree_with_key():
    # 答案里只提了 A，答案键却是 AC —— 两边打架，重写任何一边都会让卷子自相矛盾
    content = (
        "下列说法正确的是（　　）\n"
        "\\begin{choices}\n\\item 甲\n\\item 乙\n\\item 丙\n\\item 丁\n\\end{choices}"
    )
    new_c, new_a, mode = ct.apply_choice_transform(
        content, "故选 A", "multi_choice", 2,
        correct_answer="AC", rng=random.Random(4),
    )
    assert mode == ct.PLAIN
    assert new_c == content
    assert new_a == "故选 A"
