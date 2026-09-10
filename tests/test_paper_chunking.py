"""按题数再分批（split_chunks_by_question_count）的回归测试。

背景：长卷题目区字符数可能未超 split_markdown_into_question_chunks 的字符阈值，
但题数过多时单次 LLM 输出会顶到模型 max_tokens 上限被硬截断（实测 19 题卷只回收
前 10 题）。本模块用「递增题号」识别题目边界，把超限块再拆成若干子块。
"""

import mathbank.paper_chunking as pc


def _make_questions(n: int) -> str:
    """构造 n 道题的文本，题号形如「1．（5 分）…」。"""
    return "\n".join(f"{i}．（5 分）第{i}题内容 $x_{i}$" for i in range(1, n + 1))


def test_split_by_count_10_questions_becomes_2_chunks():
    text = _make_questions(10)
    chunks = pc.split_chunks_by_question_count([text], max_questions=8)
    assert len(chunks) == 2
    # 不丢字符（拼接严格等于原文）
    assert "".join(chunks) == text
    # 第一块含题 1-8，第二块含题 9-10
    assert "第1题内容" in chunks[0] and "第8题内容" in chunks[0]
    assert "第9题内容" in chunks[1] and "第10题内容" in chunks[1]


def test_split_by_count_under_limit_unchanged():
    text = _make_questions(5)
    chunks = pc.split_chunks_by_question_count([text], max_questions=8)
    assert chunks == [text]


def test_split_by_count_no_question_numbers_unchanged():
    text = "这是一段纯文字，没有任何题号，不应被切开。\n只有正文段落。"
    chunks = pc.split_chunks_by_question_count([text], max_questions=8)
    assert chunks == [text]


def test_split_by_count_zero_disables():
    text = _make_questions(20)
    chunks = pc.split_chunks_by_question_count([text], max_questions=0)
    assert chunks == [text]


def test_split_by_count_multiple_chunks_each_within_limit():
    # 19 题 → 8+8+3
    text = _make_questions(19)
    chunks = pc.split_chunks_by_question_count([text], max_questions=8)
    assert len(chunks) == 3
    assert "".join(chunks) == text
    for c in chunks:
        assert len(pc._question_number_positions(c)) <= 8


def test_question_number_positions_increasing_filter():
    # 递增过滤：混入的「回退/重复」题号（如选项里再出现的 3、1）应被剔除
    text = "1．题一内容 2．题二内容 3．干扰 3．题三内容 1．再干扰 4．题四内容"
    seq = pc._question_number_positions(text)
    nums = [n for n, _ in seq]
    assert nums == [1, 2, 3, 4]


def test_question_number_positions_ignores_decimal():
    text = "3.14 是圆周率，1．第1题 2．第2题"
    seq = pc._question_number_positions(text)
    nums = [n for n, _ in seq]
    assert nums == [1, 2]


# ---------------------------------------------------------------------------
# L1 字符硬上限 + 范围标记
# ---------------------------------------------------------------------------

def test_max_chars_per_parse_chunk_default():
    # 字符硬上限必须 <= 28000（不能比 split_markdown_into_question_chunks 的字符阈值更大）
    assert pc.DEFAULT_MAX_CHARS_PER_PARSE_CHUNK <= pc.DEFAULT_MAX_CHARS


def test_split_by_count_also_splits_by_chars():
    """8 题但单题特长（每题 ~3000 字符）→ 字符硬切应在小问边界细分。"""
    # 每题包含一个小问（1)（2)），字符硬切可按小问边界切
    long_body = "X" * 2000
    text = "\n".join(f"{i}．{long_body}\n（{i}）再{long_body}" for i in range(1, 9))
    chunks = pc.split_chunks_by_question_count([text], max_questions=8, max_chars=4000)
    # 字符硬切：每块 ≤ 4500（贪心合并有少量开销，验证切分确实触发）
    for c in chunks:
        assert len(c) <= 4500, f"块长度 {len(c)} 超字符硬上限"
    # 切分确实触发了：块数 > 1
    assert len(chunks) > 1
    # 不丢字符
    assert "".join(chunks) == text


def test_split_by_count_no_boundary_falls_back_to_original():
    """无题号/小问/段落边界时，字符硬切不切碎（与 hard_max 行为一致：原样返回）。"""
    long_q = "X" * 3000
    text = "\n".join(f"{i}．{long_q}" for i in range(1, 9))
    chunks = pc.split_chunks_by_question_count([text], max_questions=8, max_chars=12000)
    # 没有任何额外边界，字符硬切切不动 → 原样返回
    assert chunks == [text]


def test_split_by_count_disabled_chars_zero():
    """max_chars=0 → 字符硬切关闭（不向后不兼容破坏）。"""
    text = _make_questions(8)
    chunks = pc.split_chunks_by_question_count([text], max_questions=8, max_chars=0)
    assert chunks == [text]


def test_compute_chunk_scope_normal():
    text = _make_questions(10)
    assert pc.compute_chunk_scope(text) == (1, 10)


def test_compute_chunk_scope_single():
    text = "1．单题"
    assert pc.compute_chunk_scope(text) == (1, 1)


def test_compute_chunk_scope_empty():
    assert pc.compute_chunk_scope("") == (None, None)
    assert pc.compute_chunk_scope("没有任何题号") == (None, None)


def test_inject_chunk_scope_marker_normal():
    text = _make_questions(8)
    out = pc.inject_chunk_scope_marker(text)
    assert "【本段包含第 1-8 题，请全部完整输出，不要多输出】" in out
    # 不修改原文本长度异常（只多 1 行 marker）
    assert len(out) > len(text)


def test_inject_chunk_scope_marker_single_q():
    text = "1．只有一题"
    out = pc.inject_chunk_scope_marker(text)
    assert "【本段仅含第 1 题，请只输出该题，不要多输出】" in out


def test_inject_chunk_scope_marker_no_q_no_inject():
    text = "没有任何题号标记"
    assert pc.inject_chunk_scope_marker(text) == text


# ---------------------------------------------------------------------------
# L2 截断检测辅助
# ---------------------------------------------------------------------------

def test_detect_truncation_signals_complete_json():
    from mathbank.ai_json import detect_truncation_signals
    was_truncated, last_q = detect_truncation_signals(
        '{"questions":[{"content":"1. 题1"},{"content":"2. 题2"}]}'
    )
    assert was_truncated is False
    assert last_q == 2


def test_detect_truncation_signals_truncated():
    from mathbank.ai_json import detect_truncation_signals
    was_truncated, last_q = detect_truncation_signals(
        '{"questions":[{"content":"1. 题1"},{"content":"2. 题2"}'
    )
    assert was_truncated is True
    assert last_q == 2  # 从已回收的题里取最大题号


def test_detect_truncation_signals_garbage():
    from mathbank.ai_json import detect_truncation_signals
    was_truncated, last_q = detect_truncation_signals("hello world")
    assert was_truncated is True
    assert last_q is None


def test_detect_truncation_signals_empty():
    from mathbank.ai_json import detect_truncation_signals
    was_truncated, last_q = detect_truncation_signals("")
    assert was_truncated is True
    assert last_q is None


def test_detect_truncation_signals_truncated_in_string():
    """截断发生在 string 中（JSON 字符串未闭合）。"""
    from mathbank.ai_json import detect_truncation_signals
    was_truncated, last_q = detect_truncation_signals(
        '{"questions":[{"content":"1. 题1","answer_markdown":"解：xxxx'  # string 未闭合
    )
    assert was_truncated is True


# ---------------------------------------------------------------------------
# L3 任务级告警工具函数
# ---------------------------------------------------------------------------

def test_compute_missing_ranges_simple():
    import mathbank.paper_chunking as pc2
    assert pc2.compute_missing_ranges(0, 0) == []
    assert pc2.compute_missing_ranges(0, 5) == []  # expected 不可靠，不告警
    assert pc2.compute_missing_ranges(19, 19) == []  # 一致
    assert pc2.compute_missing_ranges(19, 10) == [(11, 19)]
    assert pc2.compute_missing_ranges(19, 0) == [(1, 19)]


# ---------------------------------------------------------------------------
# 分离卷解析区配对（方案A）
# ---------------------------------------------------------------------------

def _make_separated_questions(n: int) -> str:
    """构造「题干区 + 解析区」分离卷：题干题号 1..n，解析区每题含【解答】。"""
    heads = "\n".join(f"{i}．（5 分）第{i}题内容 $x_{i}$" for i in range(1, n + 1))
    answers = "\n".join(f"{i}．【解答】解：第{i}题的解析过程。" for i in range(1, n + 1))
    return heads + "\n参考答案与试题解析\n" + answers


def test_split_separated_pair_answers_to_each_block():
    """分离卷按题号窗口切分时，解析区应配对到对应题干块，而非堆到最后一块。"""
    from collections import Counter
    text = _make_separated_questions(10)
    chunks = pc.split_chunks_by_question_count([text], max_questions=8)
    assert len(chunks) == 2
    # 每块都含「本块题干 + 本块对应解析」
    assert "第1题内容" in chunks[0] and "第8题内容" in chunks[0]
    assert chunks[0].count("【解答】") == 8
    assert "第9题内容" in chunks[1] and "第10题内容" in chunks[1]
    assert chunks[1].count("【解答】") == 2
    # 字符不丢（解析区被拆开插入，顺序改变，故用 multiset 比对）
    assert Counter("".join(chunks)) == Counter(text)


def test_split_separated_under_limit_single_block():
    """分离卷题数不超限 → 单块，题干+解析整体保留。"""
    text = _make_separated_questions(5)
    chunks = pc.split_chunks_by_question_count([text], max_questions=8)
    assert chunks == [text]


def test_split_separated_answer_zone_incomplete_falls_back():
    """解析区题号识别不全（少于题干）→ 退回 tail 拼最后一块，不丢字符、不崩溃。"""
    heads = "\n".join(f"{i}．第{i}题" for i in range(1, 10))
    answers = "1．【解答】解：只有第一题的解析。"
    text = heads + "\n参考答案\n" + answers
    chunks = pc.split_chunks_by_question_count([text], max_questions=8)
    assert "".join(chunks) == text


def test_answer_zone_positions_skips_answer_values():
    """解析区「答案为：16．」「最小值为8．」「…0,1,2．」等答案值不得被误当题号。

    回归：成都七中高一（上）期末卷第 12 题答案 16 写成「故答案为：16．」，
    旧「严格递增」规则把 16 误当题号，导致 13/14/15 因「不递增」被跳过，
    19 题只识别出 16 个、配对失败回退，前几块解析全丢。
    """
    head_seq = [(i, 0) for i in range(1, 20)]  # 题干区干净题号 1..19
    tail = "\n".join(
        [
            f"{i}．【解答】解：第{i}题解析。" if i not in (12, 14, 18)
            else (
                "12．【解答】解：所以 a=16．故答案为：16．" if i == 12
                else (
                    "14．【解答】解：最小值为8．" if i == 14
                    else "18．【解答】解：解得 -1，0，1，2．"
                )
            )
            for i in range(1, 20)
        ]
    )
    seq = pc._answer_zone_question_positions(tail, head_seq)
    nums = [n for n, _ in seq]
    assert nums == list(range(1, 20))


def test_answer_zone_answer_value_not_mistaken_as_question():
    """端到端：分离卷解析区含纯数字答案时，仍应正确配对到每块（8/8/3）。"""
    from collections import Counter
    heads = "\n".join(f"{i}．（5 分）第{i}题内容 $x_{i}$" for i in range(1, 20))
    answers = []
    for i in range(1, 20):
        if i == 12:
            body = "所以 a=16．故答案为：16．"
        elif i == 14:
            body = "最小值为8．"
        elif i == 18:
            body = "解得 -1，0，1，2．"
        else:
            body = f"第{i}题解析。"
        answers.append(f"{i}．【解答】解：{body}")
    text = heads + "\n参考答案与试题解析\n" + "\n".join(answers)
    chunks = pc.split_chunks_by_question_count([text], max_questions=8)
    assert len(chunks) == 3
    assert chunks[0].count("【解答】") == 8
    assert chunks[1].count("【解答】") == 8
    assert chunks[2].count("【解答】") == 3
    assert Counter("".join(chunks)) == Counter(text)
