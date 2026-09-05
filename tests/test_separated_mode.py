"""detect_separated_mode 启发式判定的回归测试。

覆盖三轮问题：
- 2026-09-04 A1：讲义型文档（每题内联解析 + 末尾参考答案）不应被误判为分离结构
- 2026-09-05 A1-续：裸锚点「解析」误匹配数学术语「解析式 / 解析几何 / 解析法」
- 2026-09-05 A1-续：答案区比题干区还长的菁优网式试卷被漏判（旧 last_pos>=0.6 假设失效）

修复要点：
1. ``q_count`` 只统计答案区起点之前的题号
2. ``inline`` 统计范围与 ``q_count`` 对齐（旧实现全文统计，分子分母范围不一致）
3. 边界取**首个**锚点（答案区起点），不再取最后一个锚点
"""
from main import detect_separated_mode


# ---------- 讲义型（修复重点） ----------

def test_lecture_inline_with_answer_zone_returns_false():
    """10 题讲义：每题内联「解：」+ 末尾参考答案区。

    修复前：inline=10、q_count=20（含答案区 10 个题号）→ ratio=0.5 → 误判 True。
    修复后：q_count 仅统计答案区前 → q_count=10、ratio=1.0 ≥ 1.2 阈值外但仍走完流程；
           期望 False（讲义不是分离结构）。
    """
    parts = []
    for i in range(1, 11):
        parts.append(f"{i}. 已知 $f(x)={i}$，求 $f'({i})$。\n解：根据求导公式可得 $f'({i})={i}$。")
    parts.append("\n\n【答案】\n1. $f'(1)=1$\n2. $f'(2)=2$\n3. $f'(3)=3$\n4. $f'(4)=4$\n5. $f'(5)=5$\n"
                 "6. $f'(6)=6$\n7. $f'(7)=7$\n8. $f'(8)=8$\n9. $f'(9)=9$\n10. $f'(10)=10$")
    text = "\n".join(parts)
    assert detect_separated_mode(text) is False, "讲义型文档不应判为分离结构"


# ---------- 常规分离卷（保留正确行为） ----------

def test_pure_separated_layout_returns_true():
    """纯分离卷：5 题题干集中在前，答案区在后、无内联解析。

    inline_ratio=0 → < 1.2，且 last_pos ≥ 0.6、head_hits ≤ 2 → 期望 True。
    """
    stem = "\n".join(f"{i}. 已知 $f(x)=x^{i}$，求 $f'({i})$。" for i in range(1, 6))
    answer_zone = "\n".join(f"{i}. $f'({i})={i}x^{{{i-1}}}$" for i in range(1, 6))
    text = stem + "\n\n【答案】\n" + answer_zone
    assert detect_separated_mode(text) is True, "纯分离卷应判为分离结构"


# ---------- 边界 / 早返回 ----------

def test_no_answer_anchor_returns_false():
    """无答案区锚点（边讲边练、纯题干）→ 早返回 False，不应误判。"""
    text = "\n".join(f"{i}. 已知 $f(x)=x^{i}$，求 $f'({i})$。解：略。" for i in range(1, 6))
    # 「解：」不是 _ANSWER_ZONE_ANCHORS 的成员（锚点是「参考答案/【答案】」等）
    assert detect_separated_mode(text) is False


def test_anchor_too_early_returns_false():
    """答案区锚点位置 < 60% → 早返回 False（说明 last_pos 阈值仍生效）。"""
    # 答案区出现在 20% 处
    text = "1. 题干A\n2. 题干B\n【答案】\n1. 答案A\n2. 答案B" + "\n更多内容" * 100
    assert detect_separated_mode(text) is False, "答案区靠前不应判为分离结构"


# ---------- 防止回归的细节校验 ----------

def test_q_count_scoped_to_before_answer_zone_only():
    """回归保护：显式校验 q_count 只取答案区前的范围。

    构造一个答案区前后都有大量题号的样本，确保修复后函数对答案区内的
    重写题号不再敏感（不会因答案区题号膨胀导致 inline_ratio 异常稀释）。
    """
    # 答案区前 5 题（每题内联「解：」），答案区锚点在 30%，答案区内 50 个「1.」
    stem = "\n".join(f"{i}. 题干 {i}。解：略。" for i in range(1, 6))
    # 答案区再写大量「1. / 2. / ...」（噪声，模拟修复前的 bug 触发条件）
    noise = "\n".join(f"{i}. 重复答案 {i}" for i in range(1, 51))
    # 让答案区锚点出现在 ~30% 处（前置短内容 + 答案区 + 长噪声）
    text = stem + "\n\n【答案】\n" + noise

    # 修复前：q_count 含答案区 50 + 前置 5 = 55 → inline/55 ≈ 0.09 → 可能误判 True
    # 修复后：q_count 仅前置 5 → inline/5 = 1.0 ≥ 1.2 阈值外 → 但 last_pos=0.3 < 0.6 → False
    assert detect_separated_mode(text) is False


# ---------- 2026-09-05：裸锚点「解析」误匹配数学术语 ----------

def test_analytic_expression_term_not_treated_as_answer_anchor():
    """「解析式 / 解析几何 / 解析法」是数学术语，不是答案区标题。

    实测（成都七中高一期中卷）题干出现 2 次「解析式」，被裸锚点「解\\s*析」
    命中，把 head_hits 从 2 顶到 3，越过 <=2 阈值导致整卷漏判。
    """
    stem = "\n".join(
        f"{i}. 已知 $f(x)=x^{i}$，求该函数的解析式，并判断是否为解析几何问题。"
        for i in range(1, 9)
    )
    # 题干中「解析式/解析几何」密集出现，但答案区确实在后方
    answer_zone = "\n\n参考答案\n" + "\n".join(
        f"{i}. 【解答】解：由题设可得 $f'({i})={i}x^{{{i-1}}}$。" for i in range(1, 9)
    )
    text = stem + answer_zone
    assert detect_separated_mode(text) is True, (
        "数学术语『解析式』不应阻止分离判定"
    )


# ---------- 2026-09-05：答案区比题干区还长的试卷（菁优网式） ----------

def _build_jingyou_style_paper(with_footer: bool) -> str:
    """构造菁优网式试卷：题干区短、详解区长（约 1:2.4），含答案简表 + 逐题详解。"""
    stems = [f"{i}．（5分）已知函数 $f_{{{i}}}(x)$，求其定义域与值域。" for i in range(1, 20)]
    # 让 sub-parts 引入「解析式」术语，模拟真实题干
    stems[2] += "\n（2）求当 $x>0$ 时的解析式．"
    stems[14] += "\n（2）证明：该函数在定义域上单调．"
    question_zone = "\n".join(stems)

    table = "参考答案与试题解析\n一．选择题（共8小题）\n题号 " + " ".join(str(i) for i in range(1, 9))
    table += "\n答案 " + " ".join("ABCD"[i % 4] for i in range(8))
    details = "\n".join(
        f"{i}. 【解答】解：（1）由题设可知 $f_{{{i}}}(x)$ 的定义域为 $\\mathbb{{R}}$；"
        f"（2）对其求导得 $f'_{{{i}}}(x)={i}x^{{{i-1}}}$，故值域为 $[0,+\\infty)$．"
        for i in range(1, 20)
    )
    text = question_zone + "\n\n" + table + "\n\n" + details
    if with_footer:
        text += "\n\n声明：试题解析著作权属菁优网所有，未经书面同意，不得复制．"
    return text


def test_long_answer_zone_separated_paper_returns_true():
    """答案区比题干区长的分离卷应判 True（旧 last_pos>=0.6 假设失效的场景）。

    旧实现用「最后一个锚点位置 >= 0.6」判定，隐含「答案区在文档后 40%」。
    菁优网式试卷详解区常比题干区长 2~3 倍，该假设系统性失效。
    """
    text = _build_jingyou_style_paper(with_footer=True)
    assert detect_separated_mode(text) is True, (
        "答案区更长的分离卷应判为分离结构"
    )


def test_separated_detection_does_not_depend_on_copyright_footer():
    """去掉页脚版权声明后判定不变 —— 防止依赖噪声锚点。

    这是最关键的一条：只修「解析式」误匹配的话，本卷之所以能勉强判 True，
    完全靠页脚「声明：试题解析著作权属菁优网所有」(98%) 撑住 last_pos。
    换成任何一份没有这行字的卷子就立刻漏判。本测试锁定这个脆弱性。
    """
    assert detect_separated_mode(_build_jingyou_style_paper(with_footer=False)) is True


def test_inline_count_scoped_to_before_answer_zone():
    """inline 统计范围必须与 q_count 一致，否则分子分母口径不同。

    旧实现 inline 全文统计，把答案区的 19 个「【解答】解：」算进分子，
    实测把 ratio 从 0.10 抬到 1.20，越过 1.0 阈值导致漏判。
    """
    text = _build_jingyou_style_paper(with_footer=False)
    # 题干区仅 1 处「证明：」（第 15 题小问），19 题 → ratio ≈ 0.05 < 1.0
    assert detect_separated_mode(text) is True