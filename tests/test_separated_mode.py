"""detect_separated_mode 启发式判定的回归测试。

覆盖 2026-09-04 代码审查发现的 A1 问题：
- 讲义型文档（每题内联解析 + 末尾参考答案）不应被误判为分离结构
- 常规分离结构（题干在前、答案区在后）应正常判定为 True

修复点：``q_count`` 只统计答案区锚点之前的题号，不再被答案区里重写的
「1. / 2. / ...」稀释 inline_ratio。
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