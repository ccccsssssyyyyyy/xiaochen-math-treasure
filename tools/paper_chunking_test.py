"""单元测试：分块拆题 + AI JSON 截断修复（不调用真实 API）

覆盖：
1. 短文档不切块（单块）
2. 长文档 Word 风格（1. / （1））按题号边界切块，块拼接 == 原文（不丢题、不串题）
3. 长文档 LaTeX 风格（\\item）同样正确切块
4. 单题极端超长 → 段落硬切降级，拼接 == 原文
5. dedupe_questions 去重并合并图片引用（含空白归一化）
6. parse_ai_json 截断兜底：回收完整题目；正常 JSON 不变；彻底乱码仍报错
"""

from mathbank.paper_chunking import (
    dedupe_questions,
    split_markdown_into_question_chunks,
)
from mathbank.ai_json import parse_ai_json


def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        raise SystemExit(f"测试失败: {name}")


def _make_word_doc(n, per=5000):
    """构造 n 道带编号的 Word 风格题目，单题约 per 字符。"""
    parts = ["# 模拟试卷（前言）\n\n这是一段试卷说明文字。\n"]
    for i in range(1, n + 1):
        body = "（高考真题）已知 x>0，求 f(x) 的解析式，并讨论其单调性与极值。" + ("x" * max(0, per - 40))
        parts.append(f"{i}. {body}\n答案：A\n解析：略。\n")
    return "\n".join(parts)


# 1. 短文档不切块
short = "1. 题一\n2. 题二\n3. 题三\n"
c1 = split_markdown_into_question_chunks(short, max_chars=20000)
check("短文档返回单块且内容一致", c1 == [short])
check("空文本返回空列表", split_markdown_into_question_chunks("") == [])
check("None/空白安全", split_markdown_into_question_chunks("   \n  ") == [])

# 2. 长文档 Word 风格切块，拼接 == 原文
doc = _make_word_doc(8, per=5000)  # 约 40K 字符
c2 = split_markdown_into_question_chunks(doc, max_chars=15000, min_chars=100)
check("长文档被切成多块", len(c2) > 1)
check("切块拼接还原 == 原文（不丢题/不串题）", "".join(c2) == doc)
check("每块非空", all(c.strip() for c in c2))

# 3. 长文档 LaTeX 风格（\\item）切块
latex_doc = "\\begin{enumerate}\n" + "\n".join(
    f"\\item 第{i}题内容 " + ("公式 " * 3000) for i in range(1, 9)
) + "\n\\end{enumerate}\n"
c3 = split_markdown_into_question_chunks(latex_doc, max_chars=12000, min_chars=100)
check("LaTeX 文档被切成多块", len(c3) > 1)
check("LaTeX 切块拼接还原 == 原文", "".join(c3) == latex_doc)

# 4. 单题极端超长 → 段落硬切降级
huge = "1. " + ("x" * 200000)  # 单题远超 hard_max
c4 = split_markdown_into_question_chunks(huge, max_chars=15000, hard_max=40000)
check("超长单题被段落硬切为多块", len(c4) > 1)
check("硬切拼接还原 == 原文", "".join(c4) == huge)

# 5. dedupe_questions 去重 + 合并图片引用
qs = [
    {"content": "题1", "referenced_images": ["a.png"]},
    {"content": "题1", "referenced_images": ["b.png"]},  # 重复，应合并图片
    {"content": "题2"},
    {"content": "  题2  "},  # 空白归一化后重复
    {"content": ""},  # 无题干，保留原样
]
out = dedupe_questions(qs)
check("去重后剩 3 条（题1/题2/空）", len(out) == 3)
by_content = {q.get("content", "").strip(): q for q in out}
check("题1 图片引用被合并", sorted(by_content["题1"]["referenced_images"]) == ["a.png", "b.png"])
check("无题干条目被保留", any(q.get("content", "") == "" for q in out))

# 6. parse_ai_json 截断兜底
# 截断发生在数组中途（第二题已完整，其后被切断）——应回收所有完整题
truncated = '{"questions":[{"content":"题1","answer_markdown":"a1"},{"content":"题2","answer_markdown":"a2"},'
recovered = parse_ai_json(truncated)
check("截断 JSON 被回收为 dict", isinstance(recovered, dict))
check("截断 JSON 回收出 2 道完整题", len(recovered.get("questions", [])) == 2)
check("回收题目内容正确", recovered["questions"][0]["content"] == "题1")
check("回收题目保留答案字段", recovered["questions"][1]["answer_markdown"] == "a2")

# 6b. 正常 JSON 不受影响
normal = '{"questions":[{"content":"题A","answer_markdown":"ansA"}]}'
check("正常 JSON 解析不变", parse_ai_json(normal)["questions"][0]["content"] == "题A")

# 6c. 带 markdown 围栏的 JSON 仍可用
fenced = '```json\n{"questions":[{"content":"题B"}]}\n```'
check("带围栏 JSON 可解析", parse_ai_json(fenced)["questions"][0]["content"] == "题B")

# 6d. 彻底乱码仍报错（不应误回收）
raised = False
try:
    parse_ai_json("完全不是 json <<<< >>>> 乱码")
except Exception:
    raised = True
check("彻底乱码仍抛错", raised)

print("\nALL PASS: paper_chunking + ai_json 截断修复")
