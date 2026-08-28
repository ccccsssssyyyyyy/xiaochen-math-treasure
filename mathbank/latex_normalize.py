"""Normalize multiple-choice option layout into the exam-zh ``choices`` LaTeX environment.

Both the in-app KaTeX preview (``editor.js`` ``parseMarkdownWithMath`` -> ``.choices-grid``)
and the exam-zh LaTeX export auto-number each ``\\item`` with A./B./C./D., so the option body
MUST NOT contain an explicit label. This helper therefore:

* wraps inline option runs such as ``A. $...$ B. $...$`` into
  ``\\begin{choices}\\item ...\\end{choices}``;
* strips any explicit ``A./B./C./D.`` label the model left inside ``\\item``.

It is intentionally defensive: a long-form 解答题 / 填空题 without a run of consecutive
``A./B./C./D.`` options is returned unchanged, so it is safe to call on every question body
regardless of type.
"""

import re

# 选项起始：支持  A.  A、  A)  A）  （A）  (A)
# 负向后顾：前一个字符不能是单词字符或半角左括号，避免把 f(A)= 之类的数学误判为选项。
_OPTION_START = re.compile(
    r"(?<![\w（(])"
    r"(?:"
    r"（\s*([A-Ea-e])\s*）"  # 全角 （A）
    r"|"
    r"\(?\s*([A-Ea-e])\s*[\.、)）]"  # A. / A、 / A) / A） / (A)
    r")"
)

_CHOICES_ENV = re.compile(r"\\begin\{choices\}([\s\S]*?)\\end\{choices\}")

# 选项正文结尾的"答案/解析"等标记，用于截断最后一个选项之后的内容
# （这些词几乎不会出现在短选项数学中，可安全作为分段边界）。
_TRAILING_MARKERS = re.compile(r"(答案|解析|故选|参考答案)")

_LABEL_PREFIX = re.compile(
    r"^\s*(?:（\s*[A-Ea-e]\s*）|\(?\s*[A-Ea-e]\s*[\.、)）])\s*"
)


def _letter_of(match: "re.Match") -> str:
    return (match.group(1) or match.group(2) or "").upper()


def _strip_explicit_label(text: str) -> str:
    return _LABEL_PREFIX.sub("", text, count=1).strip()


def _items_from_inner(inner: str) -> list:
    # 兼容 choices 内嵌套 enumerate/itemize：先剥掉环境标记，仅留 \item
    inner = re.sub(r"\\begin\{(?:enumerate|itemize)\}", "", inner)
    inner = re.sub(r"\\end\{(?:enumerate|itemize)\}", "", inner)
    parts = inner.split(r"\item")
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        p = _strip_explicit_label(p)
        out.append(p)
    return out


def _normalize_existing_choices(content: str) -> str:
    def repl(m):
        items = _items_from_inner(m.group(1))
        return (
            "\\begin{choices}\n"
            + "\n".join(r"\item " + it for it in items)
            + "\n\\end{choices}"
        )

    return _CHOICES_ENV.sub(repl, content)


def _wrap_inline_choices(content: str) -> str:
    matches = list(_OPTION_START.finditer(content))
    if len(matches) < 2:
        return content

    # 将连续的"字母递增 1"的匹配聚合成选项段（run）
    runs: list = []
    current = [matches[0]]
    for prev, nxt in zip(matches, matches[1:]):
        if ord(_letter_of(nxt)) == ord(_letter_of(prev)) + 1:
            current.append(nxt)
        else:
            runs.append(current)
            current = [nxt]
    runs.append(current)

    replacements = []  # (start, end, replacement) —— 均基于原始 content 偏移
    for run in runs:
        if len(run) < 2:
            continue
        start = run[0].start()
        last = run[-1]
        tail = content[last.end():]
        m_tail = _TRAILING_MARKERS.search(tail)
        if m_tail:
            # 选项正文截止到答案/解析标记之前；标记及其后的整段（属答案区，不属于题干）一并丢弃
            body_end_pos = last.end() + m_tail.start()
            consume_end = len(content)
        else:
            body_end_pos = last.end() + len(tail)
            consume_end = body_end_pos
        block = content[start:body_end_pos]
        sub = list(_OPTION_START.finditer(block))
        items = []
        for i, sm in enumerate(sub):
            body_end = sub[i + 1].start() if i + 1 < len(sub) else len(block)
            body = _strip_explicit_label(block[sm.end():body_end]).strip()
            if body:
                items.append(body)
        if len(items) >= 2:
            replacement = (
                "\\begin{choices}\n"
                + "\n".join(r"\item " + it for it in items)
                + "\n\\end{choices}"
            )
            replacements.append((start, consume_end, replacement))

    if not replacements:
        return content

    # 自右向左替换，避免偏移污染
    out = content
    for s, e, r in sorted(replacements, key=lambda x: x[0], reverse=True):
        out = out[:s] + r + out[e:]
    return out


def normalize_choice_options_to_latex(content: str) -> str:
    """Return ``content`` with choice options laid out as a ``choices`` environment.

    Idempotent: content already in ``\\begin{choices}...\\end{choices}`` form (with or
    without explicit labels inside ``\\item``) is normalized, not double-wrapped.
    """
    if not isinstance(content, str):
        return ""
    if not content:
        return content
    if r"\begin{choices}" in content:
        return _normalize_existing_choices(content)
    return _wrap_inline_choices(content)
