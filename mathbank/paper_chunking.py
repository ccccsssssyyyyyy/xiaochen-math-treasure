"""试卷文本分块拆题工具。

解决「超长文档 → AI 输出超过 max_tokens 被截断 → JSON 解析失败 → 拆解失败」的根因：
将整篇试卷按题号边界切分为多个小块，逐块调用 LLM 解析，最后合并去重。
"""

import re
from typing import Any, Dict, List, Optional

# 题号起始边界：覆盖 LaTeX 枚举题号、exam 类 \question、Word/PDF 识别出的
# 「1.」「（1）」「(1)」以及带编号的 markdown 标题。
_QUESTION_START = re.compile(
    r"(?m)^[ \t]*(?:"
    r"(?:\d{1,3}[\.、）)][ \t])"          # 1. 或 1、 或 1)
    r"|(?:（\d{1,3}）)"                      # （1）
    r"|(?:\(\d{1,3}\))"                      # (1)
    r"|(?:\\item\b)"                         # \item（LaTeX enumerate）
    r"|(?:\\question\b)"                     # \question（exam 文档类）
    r"|(?:#{1,6}[ \t]+\d)"                   # markdown 标题带编号，如 ## 1.
    r")"
)

# 单块上限：输入控制在 ~28K 字符，确保付费/免费模型单块输出远小于 max_tokens。
DEFAULT_MAX_CHARS = 28000
# 单题极端超长时的硬上限：超过则按段落硬切（极少触发，属降级路径）。
DEFAULT_HARD_MAX = 80000
# 单块最小字符，避免把文档切成过多碎块。
DEFAULT_MIN_CHARS = 1200


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

    - 每块以题号起始边界开头，绝不在题目中间切断（除非单题本身超 hard_max 降级硬切）。
    - 返回空文本时返回 []；文本本身短于 max_chars 时返回 [text]。
    """
    if not text or not text.strip():
        return []
    if len(text) <= max_chars:
        return [text]

    boundaries = [m.start() for m in _QUESTION_START.finditer(text)]
    if not boundaries:
        # 没识别到题号：退化为按段落切分
        return _split_by_paragraphs(text, max_chars, hard_max)

    # 段起点：[0] + 各边界；段终点：各边界 + 文末
    seg_starts = [0] + boundaries
    seg_ends = boundaries + [len(text)]

    chunks: List[str] = []
    buf = ""
    for s, e in zip(seg_starts, seg_ends):
        seg = text[s:e]
        if not seg.strip():
            continue
        # 单段本身超硬上限：按段落硬切后并入（首段可能含前言，一并处理）
        if len(seg) > hard_max:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split_by_paragraphs(seg, max_chars, hard_max))
            continue
        # 加入本段会超限且已有缓冲：先 flush
        if buf and len(buf) + len(seg) > max_chars:
            chunks.append(buf)
            buf = seg
        else:
            buf = (buf + seg) if buf else seg

    if buf:
        chunks.append(buf)

    # 过滤空块；若块过小（< min_chars）且与下一块合并仍不超限则合并，减少碎片
    chunks = [c for c in chunks if c.strip()]
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
