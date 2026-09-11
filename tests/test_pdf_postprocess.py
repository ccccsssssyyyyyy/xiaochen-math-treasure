"""PDF 拆题后处理：题源兜底剥离 + 答案区按题号回填。

背景：用户上传的 PDF（典型为"一数 高考数学核心方法"系列）题号区在卷头，
答案区在卷尾按题号集中列 `题号. 答案\\n解析：…`。LLM 按 8 题一段切片拆题时：
- source 字段经常漏写（题源被当正文留在 content）；
- answer_markdown 也经常漏写（跨段后引用断开）。

post_process_pdf_parsed_questions 现在加了两道零 token 兜底：
1. 从题干开头正则抽 `(年份·卷种·难度)` 这种括注补到 source 字段，并把它从
   content 中剥掉，让题干回到纯净状态；
2. 用 `_extract_pdf_answer_zone` 扫 OCR 全文建题号索引，把缺失的 answer_markdown
   按题号精确回填，写入 `[EXTRACTED_ORIGINAL]` 标记，后续解析净化不会清掉。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

# macOS / Linux 默认即可，main 直接 import 会拉起 DB 等重型依赖，这里我们只测纯函数
from main import _apply_pdf_answer_zone, _extract_pdf_answer_zone


def _ocr_page(text: str) -> str:
    return f"<!-- MATHBANK_PDF_PAGE:1 -->\n{text}"


SAMPLE_OCR_PAGES = [
    # Page 1 — 题号区（题干）
    """第 1 章 集合与常用逻辑用语

模块 2 常用逻辑用语（★★）

答案：P003

1.（2025・天津卷・★）
设 $x\\in\\mathbf{R}$，则"$x=0$"是"$\\sin 2x=0$"的（  ）

2.（2024・天津卷・★）
设 $a,b\\in\\mathbf{R}$，则"$a^3=b^3$"是"$3^a=3^b$"的（  ）
""",
    # Page 2 — 题号接续
    """3.（2019・浙江卷・★★）
若 $a,b>0$，则"$a+b\\leqslant 4$"是"$ab\\leqslant 4$"的（  ）
""",
    # Page 3 — 答案区起点 header + 题号-答案
    """一数 高考数学核心方法（基础 + 中档）

1. A
解析：直接判断…

2. $\\sqrt{2} + \\frac{1}{2}$
解析：求和的最小值，可考虑凑"积定"…

3. C
解析：观察 $3-a$ 与 $a+6$ 和为定值…
""",
    # Page 4 — 答案区延伸，遇到反思段截断
    """4. $\\frac{2}{9}$
解析：观察目标分式上下变量不齐次…

【反思】
对于一元二次方程…
""",
]


def test_extract_answer_zone_recognizes_common_header():
    """答案区 header「一数 高考数学核心方法」后面紧跟「题号. 答案\\n解析：」必进答案区。"""
    zone = _extract_pdf_answer_zone(SAMPLE_OCR_PAGES)
    assert sorted(zone.keys()) == [1, 2, 3, 4]
    assert zone[1]["answer"] == "A"
    assert "直接判断" in zone[1]["explanation"]
    assert zone[2]["answer"] == "$\\sqrt{2} + \\frac{1}{2}$"
    assert zone[3]["answer"] == "C"
    assert "观察 $3-a$ 与 $a+6$" in zone[3]["explanation"]
    # 反思段把答案区截断，4 题解析存在但后续不再扫描
    assert "2/9" in zone[4]["answer"] or "\\frac{2}{9}" in zone[4]["answer"]


def test_extract_answer_zone_returns_empty_when_no_answer_section():
    """没有 header、没有「题号. 选项\\n解析：」密集出现时不识别为答案区。"""
    pages = ["1. (2024·天津卷·★)\n题目…", "2. (2024·天津卷·★)\n题目…"]
    assert _extract_pdf_answer_zone(pages) == {}


def test_extract_answer_zone_finds_stops_at_reflection_break():
    """【反思】段是答案区的天然结束符。"""
    pages = [
        """1. A
解析：解析 1

2. B
解析：解析 2

3. C
解析：解析 3 长

【反思】
…其他…
""",
    ]
    zone = _extract_pdf_answer_zone(pages)
    # 反思段之前的题号都被识别
    assert sorted(zone.keys()) == [1, 2, 3]


def test_extract_answer_zone_handles_latex_answers():
    """答案可能是 LaTeX 表达式（分数 / 根式 / 等），不能因为含 $ 而拒绝。"""
    pages = [
        """1. $\\dfrac{3}{2}$
解析：…

2. $\\sqrt{2}+1$
解析：…
""",
    ]
    zone = _extract_pdf_answer_zone(pages)
    assert zone[1]["answer"] == "$\\dfrac{3}{2}$"
    assert zone[2]["answer"] == "$\\sqrt{2}+1$"


# ──────────────────────────────────────────────────────────────────────────────
# 第二部分：post_process_pdf_parsed_questions 的端到端效果（直接复用 main 函数即可）
# ──────────────────────────────────────────────────────────────────────────────


def test_post_process_pdf_strips_source_bracket_from_content():
    """source 字段为空时，从 content 开头抽 `(年份·卷种·难度)` 兜底，并剥掉。"""
    from main import post_process_pdf_parsed_questions

    parsed_questions = [
        {
            "compulsory": "必修第一册",
            "chapter": "集合与常用逻辑用语",
            "question_type": "single_choice",
            "difficulty": "easy_error",
            "source": "",
            "content": (
                "1.（2025・天津卷・★）\n"
                "设 $x\\in\\mathbf{R}$，则\"$x=0$\"是\"$\\sin 2x=0$\"的。"
            ),
            "answer_markdown": "",
            "knowledge_list": [],
            "solve_method": "",
            "tags": [],
        },
    ]
    final = post_process_pdf_parsed_questions(
        parsed_questions, "模块 2 常用逻辑用语", task_id="fake-task-id",
        ocr_results=SAMPLE_OCR_PAGES,
    )
    q = final[0]
    # 兜底的 source 必须经规约落到 `2025·天津卷·高考真题`
    assert q["source"] == "2025 · 天津卷 · 高考真题"
    # content 开头不应再含 `(2025・天津卷・★)` 这种原始括注
    assert "（2025" not in q["content"] and "(2025" not in q["content"]
    assert "设 $x" in q["content"]
    # 临时字段必须清理
    assert "_pdf_source_extracted" not in q
    assert "_orig_seq_int" not in q


def test_post_process_pdf_fills_answer_via_question_number():
    """answer_markdown 空时按题号从答案区回填。"""
    from main import post_process_pdf_parsed_questions

    parsed_questions = [
        {
            "compulsory": "必修第一册",
            "chapter": "集合与常用逻辑用语",
            "question_type": "single_choice",
            "difficulty": "easy_error",
            "source": "",
            "content": (
                "2.（2024・天津卷・★）\n"
                "设 $a,b\\in\\mathbf{R}$，则\"$a^3=b^3$\"是\"$3^a=3^b$\"的。"
            ),
            "answer_markdown": "",
            "knowledge_list": [],
            "solve_method": "",
            "tags": [],
        },
    ]
    final = post_process_pdf_parsed_questions(
        parsed_questions, "模块 2 常用逻辑用语", task_id="fake-task-id",
        ocr_results=SAMPLE_OCR_PAGES,
    )
    q = final[0]
    # 题号 2 在答案区的答案是 $\sqrt{2}+\frac{1}{2}$ + 解析
    assert "[EXTRACTED_ORIGINAL]" in q["answer_markdown"]
    assert "$\\sqrt{2} + \\frac{1}{2}$" in q["answer_markdown"]
    assert "求和的最小值" in q["answer_markdown"]


def test_post_process_pdf_does_not_overwrite_existing_answer():
    """AI 已经填的 answer_markdown 不应被回填覆盖。"""
    from main import post_process_pdf_parsed_questions

    parsed_questions = [
        {
            "compulsory": "必修第一册",
            "chapter": "集合与常用逻辑用语",
            "question_type": "single_choice",
            "difficulty": "easy_error",
            "source": "",
            "content": "2.（2024・天津卷・★）\n题干",
            "answer_markdown": "[EXTRACTED_ORIGINAL]\nB",
            "knowledge_list": [],
            "solve_method": "",
            "tags": [],
        },
    ]
    final = post_process_pdf_parsed_parsed = post_process_pdf_parsed_questions(
        parsed_questions, "模块 2 常用逻辑用语", task_id="fake-task-id",
        ocr_results=SAMPLE_OCR_PAGES,
    )  # noqa: E501 (line break for readability)
    q = final[0]
    assert q["answer_markdown"] == "[EXTRACTED_ORIGINAL]\nB"


def test_post_process_pdf_keeps_existing_source_unchanged():
    """AI 已经正确写的 source 不应被正文兜底覆盖。"""
    from main import post_process_pdf_parsed_questions

    parsed_questions = [
        {
            "compulsory": "必修第一册",
            "chapter": "集合与常用逻辑用语",
            "question_type": "single_choice",
            "difficulty": "easy_error",
            "source": "2023 · 全国甲卷 · 高考真题",  # AI 已正确填写
            "content": (
                "1.（2025・天津卷・★）\n"
                "设 $x\\in\\mathbf{R}$…"
            ),
            "answer_markdown": "",
            "knowledge_list": [],
            "solve_method": "",
            "tags": [],
        },
    ]
    final = post_process_pdf_parsed_questions(
        parsed_questions, "模块 2 常用逻辑用语", task_id="fake-task-id",
        ocr_results=SAMPLE_OCR_PAGES,
    )
    q = final[0]
    # AI 已填的 source 应保留（避免被同卷其它题的题源覆盖）
    assert q["source"] == "2023 · 全国甲卷 · 高考真题"


def test_post_process_pdf_handles_missing_question_number():
    """题号被 LLM 吞掉时不应该错误匹配其他题的答案。"""
    from main import post_process_pdf_parsed_questions

    parsed_questions = [
        {
            "compulsory": "必修第一册",
            "chapter": "集合与常用逻辑用语",
            "question_type": "single_choice",
            "difficulty": "easy_error",
            "source": "",
            # 这里 LLM 把题号都吃了
            "content": "（2025・天津卷・★）设 $x\\in\\mathbf{R}$…",
            "answer_markdown": "",
            "knowledge_list": [],
            "solve_method": "",
            "tags": [],
        },
    ]
    final = post_process_pdf_parsed_questions(
        parsed_questions, "模块 2 常用逻辑用语", task_id="fake-task-id",
        ocr_results=SAMPLE_OCR_PAGES,
    )
    q = final[0]
    # 题号缺失，answer_markdown 不会被回填；source 仍能兜底
    assert q["answer_markdown"] == ""
    assert "天津卷" in q["source"]
