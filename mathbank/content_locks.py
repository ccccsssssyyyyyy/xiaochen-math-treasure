"""Lossless source-fragment locks for AI-assisted document splitting.

The model still sees each formula in its original sentence.  It returns only a
stable reference, and the server restores the exact source bytes afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_LOCK_TAG = "mathbank-math"


class ContentLockIntegrityError(ValueError):
    """Raised when a splitting response loses or duplicates protected content."""


@dataclass(frozen=True)
class ContentLock:
    lock_id: str
    original: str


def _is_escaped(value: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and value[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _find_math_end(value: str, start: int, delimiter: str) -> int:
    cursor = start + len(delimiter)
    while cursor < len(value):
        end = value.find(delimiter, cursor)
        if end < 0:
            return -1
        if not _is_escaped(value, end):
            return end + len(delimiter)
        cursor = end + len(delimiter)
    return -1


_MATH_ENVIRONMENTS = (
    "equation", "equation*", "align", "align*", "gather", "gather*",
    "aligned", "alignedat", "gathered", "split", "multline", "multline*",
    "eqnarray", "eqnarray*", "displaymath", "math", "array", "cases",
    "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix",
    "smallmatrix",
)


def _next_math_span(source: str, cursor: int) -> tuple[int, int] | None:
    candidates: list[tuple[int, str, str]] = []
    for opening, closing in (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]")):
        start = source.find(opening, cursor)
        while start >= 0 and _is_escaped(source, start):
            start = source.find(opening, start + len(opening))
        if start >= 0:
            candidates.append((start, opening, closing))
    for environment in _MATH_ENVIRONMENTS:
        opening = rf"\begin{{{environment}}}"
        start = source.find(opening, cursor)
        while start >= 0 and _is_escaped(source, start):
            start = source.find(opening, start + len(opening))
        if start >= 0:
            candidates.append((start, opening, rf"\end{{{environment}}}"))
    if not candidates:
        return None
    start, opening, closing = min(candidates, key=lambda item: (item[0], -len(item[1])))
    end = _find_math_end(source, start, closing)
    if end < 0:
        return None
    return start, end


def lock_visible_math(value: str, scope: str) -> tuple[str, list[ContentLock]]:
    """Wrap common balanced TeX math forms while keeping formulas visible."""
    source = str(value or "")
    safe_scope = re.sub(r"[^A-Za-z0-9_-]", "", str(scope or "DOCX"))[:48] or "DOCX"
    parts: list[str] = []
    locks: list[ContentLock] = []
    cursor = 0
    formula_index = 0

    while cursor < len(source):
        span = _next_math_span(source, cursor)
        if span is None:
            parts.append(source[cursor:])
            break
        start, end = span
        original = source[start:end]
        if not original.strip():
            parts.append(source[cursor:end])
            cursor = end
            continue

        formula_index += 1
        lock_id = f"MBM_{safe_scope}_{formula_index:04d}"
        parts.append(source[cursor:start])
        parts.append(f'<{_LOCK_TAG} id="{lock_id}">{original}</{_LOCK_TAG}>')
        locks.append(ContentLock(lock_id=lock_id, original=original))
        cursor = end

    return "".join(parts), locks


def _restore_lock_in_text(value: str, lock: ContentLock) -> tuple[str, int, bool]:
    text = str(value or "")
    token_pattern = re.compile(r"\[\[\s*" + re.escape(lock.lock_id) + r"\s*\]\]")
    tag_pattern = re.compile(
        rf"<{_LOCK_TAG}\b[^>]*\bid\s*=\s*(['\"])"
        + re.escape(lock.lock_id)
        + rf"\1[^>]*>(.*?)</{_LOCK_TAG}\s*>",
        flags=re.DOTALL | re.IGNORECASE,
    )
    token_matches = list(token_pattern.finditer(text))
    tag_matches = list(tag_pattern.finditer(text))
    count = len(token_matches) + len(tag_matches)
    modified = any(match.group(2) != lock.original for match in tag_matches)
    if count == 1:
        if token_matches:
            text = token_pattern.sub(lambda _match: lock.original, text, count=1)
        else:
            text = tag_pattern.sub(lambda _match: lock.original, text, count=1)
    return text, count, modified


def restore_visible_math(
    questions: list[dict[str, Any]],
    locks: list[ContentLock],
    strict: bool = True,
) -> dict[str, Any]:
    """Restore every lock across question content and original answers.

    In strict mode (default) each lock must appear exactly once; otherwise a
    ContentLockIntegrityError is raised.  In non-strict mode the function tries to
    recover missing or duplicated locks and returns a report with warnings so
    that callers can continue importing instead of failing the whole document.
    """
    report: dict[str, Any] = {
        "math_locks_created": len(locks),
        "math_locks_restored": 0,
        "math_locks_overwritten": 0,
        "math_locks_missing": 0,
        "math_locks_duplicated": 0,
    }
    missing: list[ContentLock] = []
    duplicated: list[str] = []

    for lock in locks:
        occurrences: list[tuple[dict[str, Any], str, str, int, bool]] = []
        total = 0
        for question in questions:
            for field in ("content", "answer_markdown"):
                field_value = question.get(field, "")
                if not isinstance(field_value, str):
                    continue
                restored, count, modified = _restore_lock_in_text(field_value, lock)
                if count:
                    occurrences.append((question, field, restored, count, modified))
                    total += count
        if total == 0:
            missing.append(lock)
            continue
        if strict and total != 1:
            duplicated.append(lock.lock_id)
            continue
        # In non-strict mode, restore every occurrence even if duplicated; the
        # formula simply appears in multiple questions, which is still correct.
        for question, field, restored, _count, modified in occurrences:
            question[field] = restored
        report["math_locks_restored"] += 1
        if modified:
            report["math_locks_overwritten"] += 1

    # Non-strict recovery: try to salvage missing locks by locating their
    # original formula text verbatim in the model output. Only restore if the
    # formula appears exactly once to avoid attaching it to the wrong question.
    still_missing: list[str] = []
    if missing and not strict:
        for lock in missing:
            found_count = 0
            found_target: tuple[dict[str, Any], str] | None = None
            for question in questions:
                for field in ("content", "answer_markdown"):
                    value = question.get(field, "")
                    if not isinstance(value, str):
                        continue
                    count = value.count(lock.original)
                    if count:
                        found_count += count
                        found_target = (question, field)
            if found_count == 1 and found_target is not None:
                question, field = found_target
                question[field] = question[field].replace(lock.original, lock.original, 1)
                report["math_locks_restored"] += 1
            else:
                still_missing.append(lock.lock_id)
        missing_ids = still_missing
    else:
        missing_ids = [lock.lock_id for lock in missing]

    report["math_locks_missing"] = len(missing_ids)
    report["math_locks_duplicated"] = len(duplicated)

    if missing_ids or duplicated:
        if strict:
            details = []
            if missing_ids:
                details.append("丢失 " + ", ".join(missing_ids[:6]))
            if duplicated:
                details.append("重复 " + ", ".join(duplicated[:6]))
            raise ContentLockIntegrityError(
                "AI 拆题时未完整保留公式定位标记（" + "；".join(details) + "），"
                "系统已停止本次结果，避免公式静默丢失或串题。"
            )
        warnings: list[str] = []
        if missing_ids:
            warnings.append(
                f"AI 拆题时有 {len(missing_ids)} 个公式锁定标记丢失，"
                "已尽量按原文恢复；请重点核对公式是否完整、是否有串题。"
            )
        if duplicated:
            warnings.append(
                f"AI 拆题时有 {len(duplicated)} 个公式锁定标记被重复使用，"
                "已在多处自动恢复。"
            )
        report["warnings"] = warnings

    return report
