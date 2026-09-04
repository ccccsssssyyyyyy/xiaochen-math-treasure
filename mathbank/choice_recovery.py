"""从原文回溯补回被大模型丢弃的选择题选项。

背景
----
整卷拆解（PDF / DOCX）由大模型把试卷原文切成结构化题目卡片。实测发现
小参数模型（如 Qwen3-VL-8B-Instruct）会**保留完整题干但系统性丢弃 A/B/C/D
选项**，即便提示词已明确要求输出 ``choices`` 环境、追加强化约束也仅从
0/8 提升到 1/8 —— 属模型能力问题，提示词无法可靠解决。

本模块用**确定性的本地回溯**兜底：拆题后，对「题型是选择题但 content 里没有
``\\begin{choices}`` 环境」的题目，回到试卷原文定位该题所在的题块，抓取其
A/B/C/D 选项行并追加回去。不消耗任何 token，与所用模型无关。

安全策略（宁可跳过，绝不误补）
------------------------------
* 只对 ``single_choice`` / ``multi_choice`` 生效；
* 题干已含 ``\\begin{choices}`` 则跳过（不覆盖模型已有的正确输出）；
* 用题干前若干「实义字符」在原文中做最长公共子串定位，匹配长度不足则跳过；
* 题块必须至少含 2 个选项，且必须是 A 开头、字母连续递增的一段；
* 题块只能被消费一次，且定位下标单调推进，防止两题抢同一块而互串选项。
"""

import difflib
import re

from mathbank.latex_normalize import normalize_choice_options_to_latex

__all__ = [
    "CHOICE_TYPES",
    "split_question_blocks",
    "extract_options",
    "recover_missing_choices",
]

CHOICE_TYPES = {"single_choice", "multi_choice"}

# 题块起始：行首 1~3 位数字 + 句点/顿号，且后面不能紧跟数字（避开 3.14、1.5万）。
# 刻意允许「1.复数」这类无空格写法——PDF 原生文本抽取出来就是这种紧凑格式。
_QUESTION_BLOCK_START = re.compile(r"(?m)^[ \t]*\d{1,3}[.、](?![0-9])")

# 选项行：A. / A、 / A． 开头，后接选项正文。
_OPTION_LINE = re.compile(r"(?m)^[ \t]*([A-Da-d])[.．、][ \t]*(.+?)[ \t]*$")

_LATEX_CMD = re.compile(r"\\[a-zA-Z]+")
_KEEP_CHAR = re.compile(r"[\u4e00-\u9fff0-9A-Za-z]")

# 取题干前多少个实义字符作为定位键
DEFAULT_KEY_CHARS = 30
# 最长公共子串至少多长才认为定位成功
DEFAULT_MIN_MATCH = 8
# 至少抓到几个选项才动手
DEFAULT_MIN_OPTIONS = 2


def _normalize_with_map(text: str):
    """去掉 LaTeX 命令与分隔符号，返回 ``(精简串, 精简下标 -> 原文下标)``。

    题干被模型加了 ``$...$`` / ``\\sqrt{}`` 等修饰，与原文逐字比对必然失败，
    故统一压成「只保留中日韩文字、数字与字母」的骨架串再匹配；同时保留下标
    映射，用于把匹配位置还原回原文坐标。
    """
    mask = bytearray(len(text))
    for match in _LATEX_CMD.finditer(text):
        for pos in range(match.start(), match.end()):
            mask[pos] = 1

    chars = []
    positions = []
    for index, char in enumerate(text):
        if mask[index] or char in "$ {}":
            continue
        if _KEEP_CHAR.match(char):
            chars.append(char)
            positions.append(index)
    return "".join(chars), positions


def _normalize(text: str) -> str:
    return _normalize_with_map(text)[0]


def split_question_blocks(source_text: str):
    """按行首题号把原文切成 ``[(start, end, text), ...]``。

    无题号（例如纯解答题集合）时返回空列表，调用方据此跳过补回。
    """
    if not source_text:
        return []
    marks = [m.start() for m in _QUESTION_BLOCK_START.finditer(source_text)]
    if not marks:
        return []
    blocks = []
    for index, start in enumerate(marks):
        end = marks[index + 1] if index + 1 < len(marks) else len(source_text)
        blocks.append((start, end, source_text[start:end]))
    return blocks


def extract_options(block_text: str, min_options: int = DEFAULT_MIN_OPTIONS):
    """从题块中抓取选项，返回 ``[(字母, 正文), ...]``；不足则返回空列表。

    只接受「A 开头且字母连续递增」的最长一段（A,B,C,D / A,B,C），
    避免把题干中孤立的 ``A.`` 误当成选项。

    字母连续性严格要求：内层循环首次遇到非预期字母即 ``break``。
    修复前 ``continue`` 会跨过无关行继续向后找「恰好等于期望字母」的项，
    导致干扰行（如同字母的「C. 干扰」 + 「C. 真选项」 + 「D. 真正丁」）
    被错误拼成 A,B,C干扰,D —— 真 C 被丢弃、D 被错连。修复后 ``break``
    保证字母链一旦断裂就停止拼接，符合模块「宁可跳过，绝不误补」原则。
    """
    if not block_text:
        return []
    matches = list(_OPTION_LINE.finditer(block_text))
    if not matches:
        return []

    best_run = []
    for start_index, match in enumerate(matches):
        if match.group(1).upper() != "A":
            continue
        run = [match]
        expected = ord("B")
        for nxt in matches[start_index + 1:]:
            if ord(nxt.group(1).upper()) == expected:
                run.append(nxt)
                expected += 1
            else:
                # 字母链断裂，停止向后拼接；与「宁可跳过绝不误补」原则一致
                break
        if len(run) > len(best_run):
            best_run = run

    if len(best_run) < min_options:
        return []
    return [(m.group(1).upper(), m.group(2).strip()) for m in best_run]


def recover_missing_choices(
    questions: list,
    source_text: str,
    key_chars: int = DEFAULT_KEY_CHARS,
    min_match: int = DEFAULT_MIN_MATCH,
    min_options: int = DEFAULT_MIN_OPTIONS,
) -> int:
    """为缺失选项的选择题从 ``source_text`` 回溯补回选项。

    就地修改 ``questions`` 中题目的 ``content``，返回成功补回的题目数。
    任何一步不满足安全条件都会静默跳过，绝不写入不确定内容。
    """
    if not questions or not source_text:
        return 0

    blocks = split_question_blocks(source_text)
    if not blocks:
        return 0

    source_clean, source_positions = _normalize_with_map(source_text)
    if not source_clean:
        return 0

    used_blocks = set()
    cursor = 0  # 题块下标单调推进，避免两题命中同一块导致选项互串
    recovered = 0

    for question in questions:
        if not isinstance(question, dict):
            continue
        if question.get("question_type") not in CHOICE_TYPES:
            continue

        content = question.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if r"\begin{choices}" in content:
            continue

        key = _normalize(content)[:key_chars]
        if len(key) < min_match:
            continue

        matcher = difflib.SequenceMatcher(None, key, source_clean, autojunk=False)
        best = matcher.find_longest_match(0, len(key), 0, len(source_clean))
        if best.size < min_match:
            continue

        origin = source_positions[best.b]
        block_index = None
        for index in range(cursor, len(blocks)):
            if blocks[index][0] <= origin < blocks[index][1]:
                block_index = index
                break
        if block_index is None or block_index in used_blocks:
            continue

        options = extract_options(blocks[block_index][2], min_options=min_options)
        if not options:
            continue

        used_blocks.add(block_index)
        cursor = block_index + 1

        option_block = "\n".join(f"{letter}.{body}" for letter, body in options)
        merged = content.rstrip() + "\n\n" + option_block
        question["content"] = normalize_choice_options_to_latex(merged)
        recovered += 1

    return recovered
