"""按「重做遍数」变换选择题选项（导出时用，**不写回库**）。

设计要点
--------
- 库内原题永远是规范顺序；每题第几次重做由调用方给出 ``attempt_no``。
- 三档：
  * 第 1 遍（``attempt_no <= 1``）：原样；
  * 第 2 遍（``== 2``）：打乱选项顺序，并**同步重映射答案键**；
  * 第 3 遍起（``>= 3``）：剥离 ``choices`` 环境，只留题干（学生回忆作答）。
- 填空题 / 解答题无选项，三档都原样。
- **安全阀**：打乱时若无法可靠定位答案里的正确字母引用（或题目没有
  ``correct_answer``），则拒绝打乱、降级为原样 —— 绝不产出「答案键对不上」的卷子。
"""

from __future__ import annotations

import random
import re

PLAIN = "plain"
SHUFFLED = "shuffled"
NO_OPTION = "no_option"

OPTION_MODE_VALUES = (PLAIN, SHUFFLED, NO_OPTION)

MCQ_TYPES = ("single_choice", "multi_choice")

_CHOICES_ENV = re.compile(r"\\begin\{choices\}([\s\S]*?)\\end\{choices\}")
_LABEL_PREFIX = re.compile(
    r"^\s*(?:（\s*[A-Ea-e]\s*）|\(?\s*[A-Ea-e]\s*[\.、)）])\s*"
)

# 答案里「正确选项」引用：故（选）A / 【答案】ACD / 答案：D / 故答案为 A、C …
# 多选必须把整串字母一起吃进来：只吃「A、C」里的 A，重映射后答案会变成
# 「故选 B、C」—— 一道自相矛盾的卷子，比不打乱危险得多。
_LETTER_RUN = r"[A-Ea-e](?:[ \t]*[、,，/]?[ \t]*[A-Ea-e])*"
_ANSWER_REF = re.compile(
    r"(?:故选|故答案为|答案为|正确答案|答案)"
    r"\s*[】\]\)）]?\s*[:：]?\s*[（(【\[]?\s*"
    r"(" + _LETTER_RUN + r")"
)
# 首行裸字母式：答案正文直接以选项字母开头，紧跟着换行或结束（``ACD\r\n\r\n由……``）。
# 尾随边界必须是换行/结尾 —— 只允许空格的话，「A 选项正确」会被误当成答案 A。
_LEADING_LETTERS = re.compile(r"^(" + _LETTER_RUN + r")[ \t]*(?=\r|\n|$)")


def is_mcq(question_type: str) -> bool:
    return (question_type or "") in MCQ_TYPES


def split_choices(content: str):
    """拆分出 ``choices`` 环境：返回 ``(before, items, after)``；无则 ``None``。"""

    if not content:
        return None
    match = _CHOICES_ENV.search(content)
    if not match:
        return None
    inner = match.group(1)
    inner = re.sub(r"\\begin\{(?:enumerate|itemize)\}", "", inner)
    inner = re.sub(r"\\end\{(?:enumerate|itemize)\}", "", inner)
    items = []
    for part in inner.split(r"\item"):
        part = part.strip()
        if not part:
            continue
        items.append(_LABEL_PREFIX.sub("", part, count=1).strip())
    if len(items) < 2:
        return None
    return (content[: match.start()], items, content[match.end():])


def _render_choices(items) -> str:
    return (
        "\\begin{choices}\n"
        + "\n".join(r"\item " + it for it in items)
        + "\n\\end{choices}"
    )


def shuffle_items(items, rng: random.Random):
    """返回 ``(new_items, order)``；``order[i]`` 是新位置 i 上原选项的下标。"""

    order = list(range(len(items)))
    rng.shuffle(order)
    return [items[i] for i in order], order


def strip_choices(content: str) -> str:
    """移除 ``choices`` 环境，只留题干。"""

    return _CHOICES_ENV.sub("", content).strip()


def _letters_of(text: str) -> str:
    """抽出选项字母，按出现顺序去重（``"A、C"`` → ``"AC"``）。"""

    seen: list[str] = []
    for ch in text or "":
        letter = ch.upper()
        if letter in "ABCDE" and letter not in seen:
            seen.append(letter)
    return "".join(seen)


def _answer_letter_span(text: str):
    """定位答案里的「正确选项」字母串，返回 ``(start, end, letters)``；找不到返回 ``None``。

    三种格式都要认 —— 这三样在真实题库里都存在，只认其中一种会让大批题永远只能
    原序重做（防不住「记住选项位置」）：

    * **首行裸字母**：``ACD\\r\\n\\r\\n由……``（字母串后直接换行，紧跟解析）
    * **引导词式**：``故选 A`` / ``【答案】ACD`` / ``答案：D``
    * **裸字母**：整段答案就是 ``ACD``

    首行式优先判：它锚在字符串开头、且要求字母后面是换行或结尾，误判面比正文里
    满世界找「答案」小得多。
    """

    if not text:
        return None
    match = _LEADING_LETTERS.match(text)
    if match:
        return (match.start(1), match.end(1), _letters_of(match.group(1)))
    match = _ANSWER_REF.search(text)
    if match:
        return (match.start(1), match.end(1), _letters_of(match.group(1)))
    return None


def extract_correct_answer(answer_markdown: str) -> str:
    """从答案文本里抽正确定盘字母（如 ``A`` / ``AC``）；抽不到返回 ``""``。

    只在导入/录入时调用一次、落到 ``questions.correct_answer``，作为第 2 遍重做
    打乱选项时重映射答案键的依据。抽不到不影响判分，只让该题降级为「原序」。
    """

    found = _answer_letter_span((answer_markdown or "").strip())
    return found[2][:5] if found else ""


def _remap_answer(answer_markdown: str, mapping: dict, expected_letters: str):
    """把答案里的正确字母引用按 ``mapping`` 重写；不可靠则返回 ``None``。

    「可靠」的判据不只是「能定位到」：定位到的字母集合必须与答案键**逐字相同**。
    否则说明卷面答案与 ``correct_answer`` 打架，此时重写任何一边都会让卷子自相矛盾
    —— 宁可拒绝打乱（降级原序），也不产出一份答案对不上的卷子。
    """

    text = answer_markdown or ""
    found = _answer_letter_span(text)
    if found is None:
        return None
    start, end, letters = found
    if letters != expected_letters:
        return None
    original = text[start:end]
    remapped = "".join(
        mapping.get(ch.upper(), ch) if ch.upper() in "ABCDE" else ch for ch in original
    )
    return text[:start] + remapped + text[end:]


def _correct_letter_mapping(items_len: int, order, correct_answer: str):
    """把「原正确字母」映射到「打乱后字母」；非法则返回 ``None``。"""

    letters = [ch for ch in (correct_answer or "").upper() if ch.isalpha()]
    if not letters:
        return None
    idx_to_new = {old_idx: chr(ord("A") + pos) for pos, old_idx in enumerate(order)}
    mapping = {}
    for ch in letters:
        old_idx = ord(ch) - ord("A")
        if old_idx < 0 or old_idx >= items_len:
            return None
        mapping[ch] = idx_to_new.get(old_idx, ch)
    return mapping


def apply_choice_transform(
    content: str,
    answer_markdown: str,
    question_type: str,
    attempt_no: int,
    correct_answer: str = "",
    rng: random.Random | None = None,
):
    """返回 ``(new_content, new_answer_markdown, option_mode)``。

    只在导出时调用；不修改数据库中的原题。任何「答案键无法可靠重映射」的情况都会
    降级为 ``plain``（原样），保证导出的卷子答案始终自洽。
    """

    answer_markdown = answer_markdown or ""
    if not is_mcq(question_type):
        return content, answer_markdown, PLAIN

    parts = split_choices(content or "")
    if parts is None:
        return content, answer_markdown, PLAIN

    before, items, after = parts

    if attempt_no <= 1:
        return content, answer_markdown, PLAIN

    if attempt_no >= 3:
        return strip_choices(content), answer_markdown, NO_OPTION

    # —— 第 2 遍：打乱（需要能可靠重映射答案键）——
    rng = rng or random.Random()
    new_items, order = shuffle_items(items, rng)
    mapping = _correct_letter_mapping(len(items), order, correct_answer)
    if mapping is None:
        return content, answer_markdown, PLAIN  # 没有 correct_answer → 降级原样
    new_answer = _remap_answer(answer_markdown, mapping, _letters_of(correct_answer))
    if new_answer is None:
        return content, answer_markdown, PLAIN  # 答案引用定位不到/与答案键不符 → 降级原样

    new_content = before + _render_choices(new_items) + after
    return new_content, new_answer, SHUFFLED
