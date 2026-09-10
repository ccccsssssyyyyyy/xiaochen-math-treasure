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


# ---------------------------------------------------------------------------
# 按题数再分批（治本「单次 LLM 输出超上限被截断 → 只回收前几题」）
# ---------------------------------------------------------------------------
# 单块最大题数：超过则强制按题号边界拆块。题数决定输出体量；即便字符阈值
# 未超（题目区往往题干短但题多），题数超限也会让单次输出顶到模型 max_tokens
# 上限被硬截断，回收逻辑只留下前面已闭合的题（实测 19 题卷只出前 10 题）。
DEFAULT_MAX_QUESTIONS = 8
# 单块字符硬上限：用于「题数少但单题特长」场景（如一道含长 LaTeX 推导的解答题）。
# 按 deepseek-flash 实际输出约 8K tokens 估算，每题 800-1500 token，保守取 12K 字符
# 对应输出约 4-6K token，给单块 8 题留余量。该值与 DEFAULT_MAX_CHARS 独立——
# DEFAULT_MAX_CHARS 管「整卷是否需要分块」，本值管「单块输出是否安全」。
DEFAULT_MAX_CHARS_PER_PARSE_CHUNK = 12000

# 灵活题号识别：不要求行首（兼容 pdf-inspector 把大题说明与题号挤进同一行、
# 以及分栏版面把题目提取成表格的情况）。用非数字前导 + 后不跟数字排除 3.14、
# 1.5万 及选项里 `A．` 这类非题号。
_QUESTION_NO = re.compile(r"(?<!\d)(\d{1,2})\s*[．.、](?![0-9])")

# 答案区锚点：与 main.py 的 _ANSWER_ZONE_ANCHORS 保持一致（拷贝以避免循环引用）。
# 分离卷的"参考答案与试题解析"区有自己的一套题号，不应被当成题目切分。
_ANSWER_ZONE_ANCHORS = re.compile(
    r"(参考答案|答案与解析|答案解析|答案与评分标准|参考答案与评分细则|"
    r"试题解析|详解与答案|参考答案及解析|答案部分|解答部分|"
    r"【答案】|【解析】|参\s*考\s*答\s*案|解\s*析(?!式|几何|法))",
    re.MULTILINE,
)


def _question_number_positions(text: str, truncate_to: Optional[int] = None) -> List[tuple]:
    """识别 text 中「连续递增」的题号，返回 [(题号, 题号起始位置), ...]。

    通过只接受「比上一个更大的题号」来过滤表格/选项里混入的干扰数字
    （如选项 `3`、幂指数 `1 2`、第 3 题选项里再次出现的 `3`），最终得到
    1,2,3,…N 的干净递增序列。无法形成递增序列时返回空列表。

    truncate_to: 可选，只识别该字符位置之前的题号（防止答案区题号混入）。
    """
    seq: List[tuple] = []
    for m in _QUESTION_NO.finditer(text or ""):
        if truncate_to is not None and m.start() >= truncate_to:
            break  # 匹配位置单调递增，越过截断点即可停止
        num = int(m.group(1))
        if not seq:
            seq.append((num, m.start()))
        elif num > seq[-1][0]:
            seq.append((num, m.start()))
    return seq


def _answer_zone_question_positions(tail: str, head_seq: List[tuple]) -> List[tuple]:
    """识别解析区（答案区）的题号边界，返回与题干区 ``head_seq`` 对齐的干净序列。

    解析区到处是「故答案为：16．」「最小值为 8．」「{…,-1,0,1,2}．」这类以
    数字结尾的句子，:func:`_question_number_positions` 的「严格递增」规则会被
    这些答案值误报带偏——第 12 题答案「16．」被误当成第 16 题号后，后续
    13/14/15 因「不递增」被跳过，19 题卷只识别出 16 个题号，配对失败回退到
    「解析堆最后一块」，前几块解析全丢。

    这里改为「按题干区题号顺序逐个对齐」：只接受等于 ``head_seq`` 里下一个
    期望题号的匹配，其余（答案值误报）一律跳过。``head_seq`` 是题干区已验证
    的干净递增题号序列（1..N），解析区题号应与之一致。解析区缺题号时返回的
    序列长度 < ``head_seq``，由调用方回退处理，不串题不崩溃。
    """
    seq: List[tuple] = []
    hi = 0
    for m in _QUESTION_NO.finditer(tail or ""):
        if hi >= len(head_seq):
            break
        num = int(m.group(1))
        if num == head_seq[hi][0]:
            seq.append((num, m.start()))
            hi += 1
    return seq


def _pair_answer_zone_to_blocks(
    head_blocks: List[str],
    head_seq: List[tuple],
    tail: str,
    max_questions: int,
) -> List[str]:
    """方案A：把「分离卷」的解析区 tail 按题号窗口切分，与题干块一一配对。

    分离卷 = 题干区 head（纯净题干）+ 解析区 tail（按题号排列的参考答案）。
    当 head 按题号切成多块后，若把 tail 整个堆到最后一块，前几块就看不到解析、
    拆出的题 ``answer_markdown`` 全空（实测 19 题卷丢前 16 题解析）。本函数把
    tail 也按同样的题号窗口切成段，拼到对应题干块，使每块都含「本块题干 +
    本块解析」。

    head_blocks: 已按 max_questions 切好的题干块（块数 = ceil(len(head_seq)/max_questions)）。
    head_seq: 题干区题号序列 [(题号, 位置), ...]。
    tail: 解析区全文（含「参考答案」标题、答案速查表等前缀，前缀归第一块）。
    max_questions: 每块题数上限。

    对齐校验：解析区题号数须不少于题干区题号数，才能按窗口精确对齐；否则
    （答案区题号识别不全/无题号）退回「tail 整个拼到最后一块」，保证不丢字符、
    不崩溃、不串题。
    """
    if not tail:
        return list(head_blocks)
    tail_seq = _answer_zone_question_positions(tail, head_seq)
    n_blocks = len(head_blocks)
    if len(tail_seq) < len(head_seq):
        # 答案区题号识别不全，退回旧逻辑：tail 整体拼到最后一块
        result = list(head_blocks)
        result[-1] = result[-1] + tail
        return result
    paired: List[str] = []
    for i in range(n_blocks):
        start_idx = i * max_questions
        end_idx = min((i + 1) * max_questions, len(tail_seq))
        # 第一块从 tail 开头起（含「参考答案」标题 + 答案速查表前缀）
        start_pos = 0 if start_idx == 0 else tail_seq[start_idx][1]
        end_pos = len(tail) if end_idx >= len(tail_seq) else tail_seq[end_idx][1]
        paired.append(head_blocks[i] + tail[start_pos:end_pos])
    return paired


def split_chunks_by_question_count(
    chunks: List[str],
    max_questions: int = DEFAULT_MAX_QUESTIONS,
    max_chars: int = DEFAULT_MAX_CHARS_PER_PARSE_CHUNK,
) -> List[str]:
    """把「题数超过 max_questions 或字符超过 max_chars」的块再拆成多个子块。

    两层防御（L1 治本）：
    - 第一层按题数拆（题号边界优先）：题号决定输出体量，题数过多单次输出
      会顶到模型 max_tokens 被硬截断（实测 19 题卷只出前 10 题）。
    - 第二层按字符拆（字符兜底）：单块字符仍超 max_chars 时，复用
      :func:`split_markdown_into_question_chunks` 在题号/小问边界再细分，
      应对「单题特长 + 总题数少」的场景（如一道含长 LaTeX 推导的大题）。

    分离卷切分修正：对每个块先用 :data:`_ANSWER_ZONE_ANCHORS` 截到题目区
    之前，**题号识别也只针对题目区**——避免答案区里的「1. A. B. C.」被
    误识别为题号（递增规则会接受答案区里的「1. 2. 3.」当题号，导致 19
    题卷被切出 5 块）。解析区（答案锚点之后）按同样的题号窗口切分并配对
    到对应题干块（方案A），使每块都含「本块题干 + 本块解析」，避免分离卷
    拆题时前几块解析丢失。

    题号识别不到的块由字符切分兜底。拼接仍保证不丢字符。
    """
    if max_questions <= 0 and max_chars <= 0:
        return chunks
    out: List[str] = []
    for chunk in chunks:
        # 分离卷修正：题目区 = 答案锚点之前，答案区 = 答案锚点之后
        ans = _ANSWER_ZONE_ANCHORS.search(chunk)
        if ans:
            head, tail = chunk[: ans.start()], chunk[ans.start():]
            head_limit = len(head)  # 题号识别只到题目区
        else:
            head, tail = chunk, ""
            head_limit = None

        # 第一层：按题数切（只切题目区 head，truncate_to 防止答案区题号混入）
        seq = _question_number_positions(head, truncate_to=head_limit) if max_questions > 0 else []
        if len(seq) > (max_questions or float("inf")):
            positions = [pos for _, pos in seq]
            boundaries = [0] + positions[max_questions::max_questions] + [len(head)]
            sub_chunks_head = [head[b1:b2] for b1, b2 in zip(boundaries, boundaries[1:])]
            # 方案A：解析区按题号窗口切分、配对到对应题干块（分离卷解析不丢）
            sub_chunks_head = _pair_answer_zone_to_blocks(sub_chunks_head, seq, tail, max_questions)
        else:
            # 题数不超限：head 与 tail 合成单块（解析区整体保留在块尾）
            sub_chunks_head = [head + tail]

        # 第二层：每个子块按字符再兜底切（在解析区配对之后，避免打乱题号窗口对齐）
        if max_chars > 0:
            further: List[str] = []
            for sc in sub_chunks_head:
                if len(sc) <= max_chars:
                    further.append(sc)
                    continue
                further.extend(split_markdown_into_question_chunks(sc, max_chars=max_chars))
            sub_chunks_head = further

        out.extend(sub_chunks_head)
    return out


def compute_chunk_scope(
    chunk: str, truncate_to: Optional[int] = None
) -> Tuple[Optional[int], Optional[int]]:
    """识别 chunk 内「连续递增题号」的起止题号，供 prompt 注入范围标记。

    找不到合法递增序列时返回 ``(None, None)``（此时不注入范围标记）。
    ``truncate_to``: 同 :func:`_question_number_positions`。
    """
    seq = _question_number_positions(chunk or "", truncate_to=truncate_to)
    if not seq:
        return (None, None)
    return (seq[0][0], seq[-1][0])


def inject_chunk_scope_marker(chunk: str) -> str:
    """在 chunk 头部加一行范围标记，便于模型明确「输出题号边界」、不"填满"输出。

    仅当 chunk 识别到合法题号区间时注入；题号识别失败保持原样。
    """
    first, last = compute_chunk_scope(chunk)
    if first is None or last is None:
        return chunk
    if first == last:
        marker = f"【本段仅含第 {first} 题，请只输出该题，不要多输出】\n"
    else:
        marker = f"【本段包含第 {first}-{last} 题，请全部完整输出，不要多输出】\n"
    return marker + chunk


def compute_missing_ranges(expected: int, parsed: int):
    """根据 expected/parsed 估算 missing 题号区间（粗略，连续段合并）。

    返回 ``[(from, to), ...]``。expected <= 0 或 parsed >= expected 时返回空列表。
    零 token 成本，供 L3 用户可见告警使用。
    单独放这里以避免 main.py 顶层 OOM 时该函数仍可被测试覆盖。
    """
    if expected <= 0 or parsed >= expected:
        return []
    return [(parsed + 1, expected)]
