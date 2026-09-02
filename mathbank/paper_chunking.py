"""试卷文本分块拆题工具。

解决「超长文档 → AI 输出超过 max_tokens 被截断 → JSON 解析失败 → 拆解失败」的根因：
将整篇试卷按题号边界切分为多个小块，逐块调用 LLM 解析，最后合并去重。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

# 大题起始边界（优先切分点）：覆盖 LaTeX 枚举题号、exam 类 \question、
# Word/PDF 识别出的「1.」「1、」「一、」以及带编号的 markdown 标题。
#
# 刻意不包含（1）/(1)/1) 这类小问号：一道解答题通常含多个小问，若把小问
# 也当作切分边界，(1) 与 (2) 会被拆到不同块，AI 只看到「(1) 求 C 的方程」
# 就当成一道完整题输出，造成跨页/跨块题目被切碎。小问号仅在单个大题自身
# 超过 max_chars 时才由 _MINOR_QUESTION_START 降级使用。
_MAJOR_QUESTION_START = re.compile(
    r"(?m)^[ \t]*(?:"
    # 1. 或 1、——后接空白/行尾，或紧跟非数字（避开 3.14、1.5万）。
    # 必须允许「1.复数」这类无空格写法：PDF 原生文本抽出来的题号就是紧凑格式，
    # 若要求题号后必须有空白，整卷会切不开、退化成段落硬切。
    r"(?:\d{1,3}[.、](?![0-9]))"
    r"|(?:[一二三四五六七八九十]{1,3}[、.])"      # 一、 二.
    r"|(?:\\item\b)"                            # \item（LaTeX enumerate）
    r"|(?:\\question\b)"                        # \question（exam 文档类）
    r"|(?:#{1,6}[ \t]+\d)"                      # markdown 标题带编号，如 ## 1.
    r")"
)

# 小问起始边界（降级切分点）：仅当某个大题段落自身超过 max_chars 时才使用，
# 默认让同一大题的 (1)(2) 留在同一块内。
_MINOR_QUESTION_START = re.compile(
    r"(?m)^[ \t]*(?:"
    r"(?:（\d{1,3}）)"                      # （1）
    r"|(?:\(\d{1,3}\))"                      # (1)
    r"|(?:\d{1,3}[）)])"                     # 1)
    r")"
)

# 单块上限：输入控制在 ~28K 字符，确保付费/免费模型单块输出远小于 max_tokens。
DEFAULT_MAX_CHARS = 28000
# 单题极端超长时的硬上限：超过则按段落硬切（极少触发，属降级路径）。
DEFAULT_HARD_MAX = 80000
# 单块最小字符，避免把文档切成过多碎块。
DEFAULT_MIN_CHARS = 1200


def _spans_by_pattern(text: str, pattern) -> List[Tuple[int, int]]:
    """按正则匹配位置把 text 切成 (start, end) 区间，首尾完整覆盖、不丢字符。"""
    boundaries = [m.start() for m in pattern.finditer(text)]
    if not boundaries:
        return []
    starts = [0] + boundaries
    ends = boundaries + [len(text)]
    return list(zip(starts, ends))


def _split_by_paragraphs(text: str, max_chars: int, hard_max: int) -> List[str]:
    """按空行切分为 <= max_chars 的块；单段超 hard_max 时按换行硬切。"""
    paragraphs = re.split(r"\n[ \t]*\n", text)
    chunks: List[str] = []
    cur = ""
    for p in paragraphs:
        if cur and len(cur) + len(p) + 2 > max_chars:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p) if cur else p
        # 单段超硬上限：按换行硬切
        while len(cur) > hard_max:
            cut = cur.rfind("\n", 0, hard_max)
            if cut <= 0:
                cut = hard_max
            chunks.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def split_markdown_into_question_chunks(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    hard_max: int = DEFAULT_HARD_MAX,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> List[str]:
    """将整篇试卷文本按题号边界切分为多块。

    切分优先级（保证同一道大题尽量完整，避免跨页/跨块题目被切碎）：

    1. 一级切分使用大题边界（``1.`` / ``一、`` / ``\\item`` / ``\\question``）；
       仅当整卷没有大题号（例如纯解答题集合）时才退回小问边界。
    2. 二级切分：某个大题段落自身超过 ``max_chars`` 时，才允许在它的小问
       边界 ``(1)``/``（1）``/``1)`` 处细分；仍切不动则按段落硬切降级。

    所有路径都保证 ``"".join(chunks) == text``（不丢题、不串题、不重复）。
    - 返回空文本时返回 []；文本本身短于 max_chars 时返回 [text]。
    """
    if not text or not text.strip():
        return []
    if len(text) <= max_chars:
        return [text]

    # 一级：大题边界优先，其次小问边界
    spans = _spans_by_pattern(text, _MAJOR_QUESTION_START)
    used_major = bool(spans)
    if not spans:
        spans = _spans_by_pattern(text, _MINOR_QUESTION_START)
    if not spans:
        # 两种题号都没识别到：退化为按段落切分
        return _split_by_paragraphs(text, max_chars, hard_max)

    # 二级：单个大题段落自身超限时才在其小问边界处细分
    refined: List[str] = []
    for s, e in spans:
        seg = text[s:e]
        if len(seg) <= max_chars:
            refined.append(seg)
            continue
        sub = _spans_by_pattern(seg, _MINOR_QUESTION_START) if used_major else []
        if len(sub) > 1:
            refined.extend([seg[a:b] for a, b in sub])
        else:
            # 小问也切不动：按段落硬切降级
            refined.extend(_split_by_paragraphs(seg, max_chars, hard_max))

    # 贪心打包：相邻小段合并到 <= max_chars，拼接保持无损
    chunks: List[str] = []
    buf = ""
    for seg in refined:
        if len(seg) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(seg)
            continue
        if buf and len(buf) + len(seg) > max_chars:
            chunks.append(buf)
            buf = seg
        else:
            buf = (buf + seg) if buf else seg

    if buf:
        chunks.append(buf)

    # 只丢弃空串（保留纯空白段，确保拼接严格等于原文）
    chunks = [c for c in chunks if c]
    # 碎片合并：过小的块与后续块合并仍不超限则合并，减少 LLM 调用次数
    merged: List[str] = []
    for c in chunks:
        if merged and len(merged[-1]) < min_chars and len(merged[-1]) + len(c) <= max_chars:
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    return merged


def dedupe_questions(questions: List[Any]) -> List[Any]:
    """按题干内容归一化去重（跨块/跨次解析可能重复），并合并图片引用。"""
    if not isinstance(questions, list):
        return questions
    seen: Dict[str, dict] = {}
    order: List[Any] = []
    for q in questions:
        if not isinstance(q, dict):
            # 非字典对象（异常）原样保留，不丢
            order.append(q)
            continue
        content = q.get("content", "") or ""
        key = re.sub(r"\s+", " ", content).strip()
        if not key:
            # 无题干无法去重，原样保留
            order.append(q)
            continue
        if key in seen:
            existing = seen[key]
            # 合并图片引用，避免去重丢图
            ev = existing.get("referenced_images")
            if not isinstance(ev, list):
                existing["referenced_images"] = ev = []
            nv = q.get("referenced_images")
            if isinstance(nv, list):
                for v in nv:
                    if v not in ev:
                        ev.append(v)
            continue
        seen[key] = q
        order.append(q)
    return order
